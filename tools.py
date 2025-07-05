import logging
from livekit.agents import function_tool, RunContext
import pymysql.cursors
import requests
from langchain_community.tools import DuckDuckGoSearchRun
import os
import smtplib
from email.mime.multipart import MIMEMultipart  
from email.mime.text import MIMEText
from typing import Optional, Dict, List
from dotenv import load_dotenv
import json
import sqlite3
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
import uuid
import pymysql

load_dotenv()

#creating connection

def get_connection():
    return pymysql.connect(
        host = 'localhost',
        user = 'root',
        password = '1234',
        database= 'appointments',
        cursorclass= pymysql.cursors.DictCursor
    )

# Database setup
def init_database():
    """Initialize the appointments database"""
    conn = get_connection()
    with conn.cursor() as cursor:
    
        # Create appointments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS appointments (
                id VARCHAR(255) PRIMARY KEY,
                client_name VARCHAR(255) NOT NULL,
                client_email VARCHAR(255) NOT NULL,
                appointment_with VARCHAR(255) NOT NULL,
                appointment_with_email VARCHAR(255) NOT NULL,
                appointment_date VARCHAR(255) NOT NULL,
                appointment_time VARCHAR(255) NOT NULL,
                status VARCHAR(50) DEFAULT 'scheduled',
                google_event_id VARCHAR(255),
                meet_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
        # Create availability table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS availability (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                person_name VARCHAR(255) NOT NULL,
                person_email VARCHAR(255) NOT NULL,
                date VARCHAR(255) NOT NULL,
                time_slot VARCHAR(255) NOT NULL,
                is_available BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Create people directory table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS people_directory (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                role VARCHAR(255),
                department VARCHAR(255)
            )
        ''')
    
    conn.commit()
    conn.close()

# Initialize database on import
# init_database()

# Google Calendar setup
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_google_calendar_service():
    """Get Google Calendar service"""
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    return build('calendar', 'v3', credentials=creds)

@function_tool()
async def search_person(
    context: RunContext,
    query: str
) -> str:
    """
    Search for a person in the directory by name or role.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
        
            # Search by name or role
            cursor.execute('''
                SELECT name, email, role, department 
                FROM people_directory 
                WHERE name LIKE %s OR role LIKE %s OR department LIKE %s
            ''', (f'%{query}%', f'%{query}%', f'%{query}%'))
            
            results = cursor.fetchall()
        conn.close()
        
        if not results:
            return f"No person found matching '{query}'. Please specify the full name or try a different search term."
        
        if len(results) == 1:
            person = results[0]
            return f"Found: {person['name']} ({person['role']}) - {person['department']} department. Email: {person['email']}"
        else:
            # Multiple results
            people_list = []
            for person in results:
                people_list.append(f"{person['name']} ({person['role']}) - {person['department']}")
            return f"Found multiple people: {', '.join(people_list)}. Please specify which one you'd like to book with."
    
    except Exception as e:
        logging.error(f"Error searching for person: {e}")
        return f"An error occurred while searching for '{query}'."

@function_tool()
async def check_availability(
    context: RunContext,
    person_email: str,
    date: str,
    time: str = None
) -> str:
    """
    Check availability for a person on a specific date and time.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
        
            if time:
                # Check specific time slot
                cursor.execute('''
                    SELECT * FROM appointments 
                    WHERE appointment_with_email = %s AND appointment_date = %s AND appointment_time = %s
                ''', (person_email, date, time))
                
                existing = cursor.fetchone()
                if existing:
                    return f"Sorry, that time slot is already booked. Please choose a different time."
                else:
                    return f"Time slot is available for booking."
            else:
                # Check all appointments for the day
                cursor.execute('''
                    SELECT appointment_time, client_name FROM appointments 
                    WHERE appointment_with_email = %s AND appointment_date = %s
                    ORDER BY appointment_time
                ''', (person_email, date))
                
                appointments = cursor.fetchall()
                
                if not appointments:
                    return f"No appointments scheduled for {date}. The day is completely free."
                else:
                    booked_times = [f"{appt['appointment_time']} (with {appt['client_name']})" for appt in appointments]
                    return f"Booked times for {date}: {', '.join(booked_times)}"
    
    except Exception as e:
        logging.error(f"Error checking availability: {e}")
        return f"An error occurred while checking availability."
    finally:
        conn.close()

@function_tool()
async def create_appointment(
    context: RunContext,
    client_name: str,
    client_email: str,
    appointment_with: str,
    appointment_with_email: str,
    appointment_date: str,
    appointment_time: str,
    meeting_title: str = None
) -> str:
    """
    Create a new appointment and add it to the database.
    """
    try:
        appointment_id = str(uuid.uuid4())
        
        # Check if time slot is available
        availability_check = await check_availability(
            context, appointment_with_email, appointment_date, appointment_time
        )
        
        if "already booked" in availability_check:
            return availability_check
        
        # Create Google Calendar event and Meet link
        meet_link = ""
        event_id = ""
        
        try:
            service = get_google_calendar_service()
            
            # Parse datetime
            appointment_datetime = datetime.strptime(f"{appointment_date} {appointment_time}", "%Y-%m-%d %H:%M")
            end_datetime = appointment_datetime + timedelta(hours=1)  # Default 1 hour meeting
            
            event = {
                'summary': meeting_title or f"Meeting with {client_name}",
                'description': f"Appointment scheduled via AI Receptionist\nClient: {client_name}\nEmail: {client_email}",
                'start': {
                    'dateTime': appointment_datetime.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
                'end': {
                    'dateTime': end_datetime.isoformat(),
                    'timeZone': 'Asia/Kolkata',
                },
                'attendees': [
                    {'email': client_email},
                    {'email': appointment_with_email},
                ],
                'conferenceData': {
                    'createRequest': {
                        'requestId': appointment_id,
                        'conferenceSolutionKey': {
                            'type': 'hangoutsMeet'
                        }
                    }
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'email', 'minutes': 24 * 60},
                        {'method': 'popup', 'minutes': 10},
                    ],
                },
            }
            
            # Create the event
            event = service.events().insert(
                calendarId='primary',
                body=event,
                conferenceDataVersion=1
            ).execute()
            
            event_id = event['id']
            meet_link = event.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', '')
            
        except Exception as e:
            logging.error(f"Error creating Google Calendar event: {e}")
            # Continue with database creation even if calendar fails
        
        # Save to database
        conn = get_connection()
        with conn.cursor() as cursor:
        
            cursor.execute('''
                INSERT INTO appointments 
                (id, client_name, client_email, appointment_with, appointment_with_email, 
                appointment_date, appointment_time, google_event_id, meet_link)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (appointment_id, client_name, client_email, appointment_with, 
                appointment_with_email, appointment_date, appointment_time, event_id, meet_link))
        
        conn.commit()
        conn.close()
        
        logging.info(f"Appointment created: {appointment_id}")
        return f"Appointment successfully created! Meeting ID: {appointment_id[:8]}. Calendar invite and Google Meet link will be sent via email."
    
    except Exception as e:
        logging.error(f"Error creating appointment: {e}")
        return f"An error occurred while creating the appointment: {str(e)}"

@function_tool()
async def send_appointment_email(
    context: RunContext,
    appointment_id: str
) -> str:
    """
    Send appointment confirmation emails to both client and the person they're meeting with.
    """
    try:
        # Get appointment details
        conn = get_connection()
        with conn.cursor() as cursor:
        
            cursor.execute('''
                SELECT client_name, client_email, appointment_with, appointment_with_email,
                    appointment_date, appointment_time, meet_link
                FROM appointments WHERE id = %s
            ''', (appointment_id,))
            
            appointment = cursor.fetchone()
        conn.close()
        
        if not appointment:
            return "Appointment not found."
        
        client_name = appointment['client_name']
        client_email = appointment['client_email']
        appointment_with = appointment['appointment_with']
        appointment_with_email = appointment['appointment_with_email']
        appointment_date = appointment['appointment_date']
        appointment_time = appointment['appointment_time']
        meet_link = appointment['meet_link']
        
        # Gmail SMTP configuration
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        gmail_user = os.getenv("GMAIL_USER")
        gmail_password = os.getenv("GMAIL_APP_PASSWORD")
        
        if not gmail_user or not gmail_password:
            return "Email sending failed: Gmail credentials not configured."
        
        # Email to client
        client_subject = f"Appointment Confirmation - Meeting with {appointment_with}"
        client_message = f"""
Dear {client_name},

Your appointment has been confirmed!

Meeting Details:
- Date: {appointment_date}
- Time: {appointment_time}
- Meeting with: {appointment_with}
- Google Meet Link: {meet_link if meet_link else 'Will be provided separately'}

Please join the meeting using the Google Meet link provided above.

If you need to reschedule or cancel, please contact us as soon as possible.

Best regards,
AI Receptionist
        """
        
        # Email to the person being met with
        person_subject = f"New Appointment Scheduled - Meeting with {client_name}"
        person_message = f"""
Dear {appointment_with},

A new appointment has been scheduled with you.

Meeting Details:
- Date: {appointment_date}
- Time: {appointment_time}
- Client: {client_name}
- Client Email: {client_email}
- Google Meet Link: {meet_link if meet_link else 'Will be provided separately'}

The meeting details have been added to your calendar.

Best regards,
AI Receptionist
        """
        
        # Send emails
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(gmail_user, gmail_password)
        
        # Send to client
        client_msg = MIMEMultipart()
        client_msg['From'] = gmail_user
        client_msg['To'] = client_email
        client_msg['Subject'] = client_subject
        client_msg.attach(MIMEText(client_message, 'plain'))
        
        server.sendmail(gmail_user, [client_email], client_msg.as_string())
        
        # Send to person
        person_msg = MIMEMultipart()
        person_msg['From'] = gmail_user
        person_msg['To'] = appointment_with_email
        person_msg['Subject'] = person_subject
        person_msg.attach(MIMEText(person_message, 'plain'))
        
        server.sendmail(gmail_user, [appointment_with_email], person_msg.as_string())
        
        server.quit()
        
        logging.info(f"Appointment emails sent for {appointment_id}")
        return "Appointment confirmation emails sent successfully to both parties."
    
    except Exception as e:
        logging.error(f"Error sending appointment emails: {e}")
        return f"An error occurred while sending appointment emails: {str(e)}"

@function_tool()
async def add_person_to_directory(
    context: RunContext,
    name: str,
    email: str,
    role: str,
    department: str = "General"
) -> str:
    """
    Add a person to the directory for future appointments.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
        
            cursor.execute('''
                INSERT INTO people_directory (name, email, role, department)
                VALUES (%s, %s, %s, %s)
            ''', (name, email, role, department))
        
        conn.commit()
        conn.close()
        
        return f"Successfully added {name} ({role}) to the directory."
    
    except Exception as e:
        logging.error(f"Error adding person to directory: {e}")
        return f"An error occurred while adding person to directory: {str(e)}"

@function_tool()
async def get_appointments_for_date(
    context: RunContext,
    date: str,
    person_email: str = None
) -> str:
    """
    Get all appointments for a specific date, optionally filtered by person.
    """
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
        
            if person_email:
                cursor.execute('''
                    SELECT client_name, appointment_time, appointment_with
                    FROM appointments 
                    WHERE appointment_date = %s AND appointment_with_email = %s
                    ORDER BY appointment_time
                ''', (date, person_email))
            else:
                cursor.execute('''
                    SELECT client_name, appointment_time, appointment_with
                    FROM appointments 
                    WHERE appointment_date = %s
                    ORDER BY appointment_time
                ''', (date,))
            
            appointments = cursor.fetchall()
        conn.close()
        
        if not appointments:
            return f"No appointments scheduled for {date}."
        
        appointment_list = []
        for appt in appointments:
            appointment_list.append(f"{appt['appointment_time']} - {appt['client_name']} meeting with {appt['appointment_with']}")
        
        return f"Appointments for {date}:\n" + "\n".join(appointment_list)
    
    except Exception as e:
        logging.error(f"Error getting appointments: {e}")
        return f"An error occurred while retrieving appointments: {str(e)}"