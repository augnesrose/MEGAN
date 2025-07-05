import sqlite3
import pymysql

def populate_sample_data():
    def get_connection():
        return pymysql.connect(
        host = 'localhost',
        user = 'root',
        password = '1234',
        database= 'appointments',
        cursorclass= pymysql.cursors.DictCursor
    )
    
    sample_people = [
        ('John Smith', 'john.smith@company.com', 'CEO', 'Executive'),
        ('Sarah Johnson', 'sarah.johnson@company.com', 'CTO', 'Technology'),
        ('Mike Chen', 'mike.chen@company.com', 'Head of Sales', 'Sales'),
        ('Emily Davis', 'emily.davis@company.com', 'HR Director', 'Human Resources'),
        ('David Wilson', 'david.wilson@company.com', 'Product Manager', 'Product'),
    ]
    
    for name, email, role, department in sample_people:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO people_directory (name, email, role, department)
                VALUES (%s, %s, %s, %s)
            ''', (name, email, role, department))
    
        conn.commit()
        conn.close()
    print("Sample data populated!")

if __name__ == "__main__":
    populate_sample_data()