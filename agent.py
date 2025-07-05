from dotenv import load_dotenv
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import noise_cancellation, google
from prompts import RECEPTIONIST_INSTRUCTION, SESSION_INSTRUCTION
from tools import (
    search_person,
    check_availability,
    create_appointment,
    send_appointment_email,
    add_person_to_directory,
    get_appointments_for_date
)

load_dotenv()

class ReceptionistAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=RECEPTIONIST_INSTRUCTION,
            llm=google.beta.realtime.RealtimeModel(
                voice="Aoede",
                temperature=0.7,  # Slightly lower for more consistent responses
            ),
            tools=[
                # Appointment management tools
                search_person,
                check_availability,
                create_appointment,
                send_appointment_email,
                add_person_to_directory,
                get_appointments_for_date,
            ],
        )

async def entrypoint(ctx: agents.JobContext):
    session = AgentSession()

    await session.start(
        room=ctx.room,
        agent=ReceptionistAgent(),
        room_input_options=RoomInputOptions(
            video_enabled=True,
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()

    await session.generate_reply(
        instructions=SESSION_INSTRUCTION,
    )

if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))