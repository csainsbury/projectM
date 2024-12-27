import requests
import json
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get API key from environment variable
API_KEY = os.getenv('TODOIST_API_KEY')
OUTPUT_FOLDER = '/Users/csainsbury/Documents/projectM/tasks'  # Change to your desired folder path

def export_todoist():
    url = "https://api.todoist.com/rest/v2/tasks"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        tasks = response.json()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"{OUTPUT_FOLDER}/inbox_tasks.json"
        
        with open(output_file, "w") as file:
            json.dump(tasks, file, indent=4)
        print(f"Tasks exported to {output_file}")
    else:
        print(f"Failed to fetch tasks: {response.status_code}, {response.text}")

if __name__ == "__main__":
    export_todoist()