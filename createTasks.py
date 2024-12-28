import json
import os
from datetime import datetime
from todoist_api_python.api import TodoistAPI
import logging
from typing import Dict, List, Set
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class TaskManager:
    def __init__(self, api_token: str):
        self.api = TodoistAPI(api_token)
        self.tasks_file = "tasks/inbox_tasks.json"
        self.completed_file = "tasks/completed_tasks.json"

    def update_tasks(self) -> None:
        """Update tasks and track completions"""
        try:
            # Load existing tasks
            existing_tasks = self._load_existing_tasks()
            # Handle both list and dict formats
            existing_task_list = existing_tasks if isinstance(existing_tasks, list) else existing_tasks.get('tasks', [])
            existing_task_ids = {task['id'] for task in existing_task_list}

            # Get current tasks from Todoist
            current_tasks = self._get_todoist_tasks()
            current_task_ids = {task['id'] for task in current_tasks}

            # Identify completed tasks (in existing but not in current)
            completed_task_ids = existing_task_ids - current_task_ids
            completion_time = int(datetime.now().timestamp())

            if completed_task_ids:
                # Get completed task details from existing tasks
                completed_tasks = [
                    {
                        **task,
                        'completed_at': completion_time,
                        'completion_source': 'todoist'
                    }
                    for task in existing_task_list
                    if task['id'] in completed_task_ids
                ]

                # Store completed tasks
                self._store_completed_tasks(completed_tasks)
                logger.info(f"Stored {len(completed_tasks)} completed tasks")

            # Update current tasks file
            self._store_current_tasks({'tasks': current_tasks})
            logger.info(f"Updated tasks file with {len(current_tasks)} tasks")

        except Exception as e:
            logger.error(f"Error updating tasks: {e}")
            raise

    def _get_todoist_tasks(self) -> List[Dict]:
        """Get tasks from Todoist API"""
        try:
            tasks = self.api.get_tasks()
            return [
                {
                    'id': str(task.id),
                    'content': task.content,
                    'description': task.description,
                    'priority': task.priority,
                    'due': task.due.date if task.due else None,
                    'labels': task.labels,
                    'created_at': int(datetime.strptime(task.created_at.replace('Z', '+00:00'), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp())
                }
                for task in tasks
            ]
        except Exception as e:
            logger.error(f"Error getting Todoist tasks: {e}")
            raise

    def _load_existing_tasks(self) -> Dict:
        """Load existing tasks from file"""
        try:
            if os.path.exists(self.tasks_file):
                with open(self.tasks_file, 'r') as f:
                    return json.load(f)
            return {'tasks': []}
        except Exception as e:
            logger.error(f"Error loading existing tasks: {e}")
            return {'tasks': []}

    def _store_current_tasks(self, tasks: Dict) -> None:
        """Store current tasks to file"""
        try:
            os.makedirs(os.path.dirname(self.tasks_file), exist_ok=True)
            with open(self.tasks_file, 'w') as f:
                json.dump(tasks, f, indent=2)
        except Exception as e:
            logger.error(f"Error storing current tasks: {e}")
            raise

    def _store_completed_tasks(self, completed_tasks: List[Dict]) -> None:
        """Store completed tasks in completed_tasks.json"""
        try:
            # Load existing completed tasks
            existing_completed = []
            if os.path.exists(self.completed_file):
                with open(self.completed_file, 'r') as f:
                    existing_completed = json.load(f)

            # Add new completed tasks
            existing_completed.extend(completed_tasks)

            # Store updated completed tasks
            with open(self.completed_file, 'w') as f:
                json.dump(existing_completed, f, indent=2)

        except Exception as e:
            logger.error(f"Error storing completed tasks: {e}")
            raise

def main():
    """Main execution"""
    try:
        # Load API token from environment
        api_token = os.getenv('TODOIST_API_KEY')
        if not api_token:
            raise ValueError("TODOIST_API_KEY not found in environment")

        # Initialize task manager and update tasks
        task_manager = TaskManager(api_token)
        task_manager.update_tasks()

    except Exception as e:
        logger.error(f"Error in main execution: {e}")
        raise

if __name__ == "__main__":
    main()