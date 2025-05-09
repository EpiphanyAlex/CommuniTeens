from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import json
from backend.models.models import db, Playground, MelFeature, Event
from backend.config.db_config import SQLALCHEMY_DATABASE_URI
#from backend.routes.chat import chat_bp
from backend.routes.main import main
from backend.routes.update_events import fetch_and_store_events, should_update
from backend.routes.import_csv import csv_import
from backend.routes.playgrounds import playgrounds_bp
from openai import OpenAI
from dotenv import load_dotenv
import logging
from datetime import datetime

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# OpenAI API key
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# System prompt for the chatbot
SYSTEM_PROMPT = """
You are a Melbourne Community Connections Helper, designed for youth aged 12-18. 
Your goals are to:
1. provide information about local events, resources and opportunities in Melbourne
2. encourage community engagement and social connection
3. recommend local activities for teens
4. provide advice and resources for coping with social isolation
5. respond in a friendly, supportive manner, appropriate to the youth's language style
6. refrain from discussing topics that are inappropriate for minors
"""

conversation_history = {}

def create_app():
    app = Flask(__name__)
    CORS(app)

    app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)

    app.register_blueprint(main)
    app.register_blueprint(csv_import)
    app.register_blueprint(playgrounds_bp)

    # try:
    #     with app.app_context():
    #         db.create_all()
    #         print("Database tables created successfully")
    # except Exception as e:
    #     print(f"Could not connect to database: {e}")

    @app.route('/api/chat', methods=['POST'])
    def chat():
        try:
            data = request.json
            user_message = data.get('message', '')
            session_id = data.get('session_id', data.get('conversationId', 'default'))

            logger.info(f"Received message from session {session_id}: {user_message}")

            if session_id not in conversation_history:
                conversation_history[session_id] = []

            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history[session_id]
            messages.append({"role": "user", "content": user_message})

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=1000,
                temperature=0.7,
            )

            assistant_response = response.choices[0].message.content

            conversation_history[session_id].append({"role": "user", "content": user_message})
            conversation_history[session_id].append({"role": "assistant", "content": assistant_response})

            if len(conversation_history[session_id]) > 10:
                conversation_history[session_id] = conversation_history[session_id][-10:]

            return jsonify({
                "response": assistant_response,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }
            })

        except Exception as e:
            logger.error(f"Error in chat endpoint: {str(e)}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})

    @app.route('/events', methods=['GET'])
    def get_events():
        events = Event.query.all()
        if not events:
            # If there is no data in the database, return to local eventlist.json
            with open('eventlist.json', 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            return jsonify(backup_data)
        else:
            return jsonify({"eventList": [event.to_dict() for event in events]})

    # id
    @app.route('/events/<int:event_id>', methods=['GET'])
    def get_event(event_id):
        event = Event.query.get(event_id)
        if event is None:
            return jsonify({'error': 'Event not found'}), 404
        return jsonify(event.to_dict())

    @app.route('/events/within', methods=['GET'])
    def get_events_within_bounds():
        try:
            min_lat = float(request.args.get('min_lat'))
            max_lat = float(request.args.get('max_lat'))
            min_lng = float(request.args.get('min_lng'))
            max_lng = float(request.args.get('max_lng'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid or missing parameters'}), 400

        events = Event.query.filter(
            Event.latitude >= min_lat,
            Event.latitude <= max_lat,
            Event.longitude >= min_lng,
            Event.longitude <= max_lng
        ).all()

        return jsonify({
            "eventList": [event.to_dict() for event in events]
        })

    # http://127.0.0.1:5000/events/nearby?lat=-37.8136&lng=144.9631
    @app.route('/events/nearby', methods=['GET'])
    def get_events_nearby():
        try:
            lat = float(request.args.get('lat'))
            lng = float(request.args.get('lng'))
        except (TypeError, ValueError):
            return jsonify({'error': 'Missing or invalid lat/lng'}), 400

        # Approximate offset of 5 km (applicable to Melbourne)
        lat_range = 0.045
        lng_range = 0.056

        min_lat = lat - lat_range
        max_lat = lat + lat_range
        min_lng = lng - lng_range
        max_lng = lng + lng_range

        # Fast range query
        events = Event.query.filter(
            Event.latitude >= min_lat,
            Event.latitude <= max_lat,
            Event.longitude >= min_lng,
            Event.longitude <= max_lng
        ).all()

        return jsonify({
            "eventList": [event.to_dict() for event in events]
        })
    
    @app.route('/api/update-events', methods=['GET'])
    def update_events_route():
        try:
            # from backend.routes.update_events import should_update, fetch_and_store_events

            from datetime import date
            from backend.models.models import EventUpdateStatus
            
            status = EventUpdateStatus.query.first()
            need_update = True if not status else status.last_updated != date.today()
            
            if need_update:
                fetch_and_store_events()
                return jsonify({"message": "Events updated successfully"})
            else:
                return jsonify({"message": "Events already up to date"})
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"Error in update_events_route: {error_details}")
            return jsonify({"error": str(e)}), 500
    # with app.app_context():
    #     # db.create_all()
    #     # print("Database tables created successfully")

    #     if should_update():
    #         fetch_and_store_events()
    #     else:
    #         print("Event data already up to date.")

    return app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
