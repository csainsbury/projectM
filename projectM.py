#!/usr/bin/env python3
"""
Multi-Project Task Management Engine

This code implements the functionality defined in the pseudo code, leveraging
a Gemini API call for LLM suggestions, and storing data/feedback in a GitHub repository.
It provides:
  - Classes for analyzing tasks, GitHub activity, user interactions, temporal patterns, etc.
  - A feedback collector and repository for reading/writing JSON/JSONL files.
  - An LLM service that calls the Gemini model API.
  - An action suggestion system that orchestrates the entire process.

Usage:
  - You can invoke process_query(...) on the ActionSuggestionSystem to get
    suggestions for the next actions based on active tasks, historical feedback, etc.
  - You can call update_feedback() to periodically collect and store feedback in the repo.
  - You can schedule hourly_task_sync() and daily_pattern_analysis() to run at appropriate intervals.

Assumptions / Placeholders:
  - The Gemini API key is assumed to be known and set (replace YOUR_KEY_HERE with your real key).
  - Reading/writing from local JSON/JSONL files in the `data/` directory is used to simulate
    a GitHub repository. In a real production environment, you could replace these with direct
    GitHub API calls or local `git` commands to commit/push changes.
  - For GitHub commits, we provide placeholders in `commit_to_github(...)` methods.

"""

import json
import jsonlines
import os
import time
import requests
from datetime import datetime
from github import Github
from github.Repository import Repository
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ---------------------------------------------------------------------------
# Data file paths (in a real scenario, these might map to your GitHub repo)
# ---------------------------------------------------------------------------
DATA_DIR = "data"
ACTION_OUTCOMES_FILE = os.path.join(DATA_DIR, "action_outcomes.jsonl")
SUCCESS_PATTERNS_FILE = os.path.join(DATA_DIR, "success_patterns.json")
TEMPORAL_ANALYSIS_FILE = os.path.join(DATA_DIR, "temporal_analysis.json")


# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# GitHub Configuration 
# ---------------------------------------------------------------------------
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable is not set")

GITHUB_REPO = "csainsbury/projectM"  # e.g. "username/project-repo"
TASKS_PATH = "tasks"
FEEDBACK_DIR = "feedback"


# ---------------------------------------------------------------------------
# CLASS: TaskAnalysisService
# ---------------------------------------------------------------------------
class TaskAnalysisService:
    """
    Compares previous tasks to current tasks to identify completed, modified, or
    newly created tasks. Also can analyze how completion might affect downstream tasks.
    """

    def monitor_task_changes(self, previous_tasks, current_tasks):
        """Compare task lists to identify completed and modified tasks."""
        previous_ids = {task['id'] for task in previous_tasks}
        current_ids = {task['id'] for task in current_tasks}

        completed = previous_ids - current_ids
        modified = []
        downstream = []

        # Replace placeholder logic with actual dependency checking
        for ctask in current_tasks:
            # Check for modified tasks
            for ptask in previous_tasks:
                if (ctask['id'] == ptask['id'] and 
                    (ctask.get('description') != ptask.get('description') or
                     ctask.get('status') != ptask.get('status') or
                     ctask.get('priority') != ptask.get('priority'))):
                    modified.append(ctask['id'])
            
            # Check for downstream dependencies
            if 'depends_on' in ctask:
                if any(dep_id in completed for dep_id in ctask['depends_on']):
                    downstream.append(ctask['id'])

        return {
            'completed': list(completed),
            'modified': modified,
            'downstream': downstream
        }

    def analyze_completion_patterns(self, task_history):
        """Analyze patterns in task completion"""
        if not task_history:
            return {
                'avg_completion_time_hours': None,
                'success_rate': 0,
                'completion_by_priority': {},
                'completion_by_type': {},
                'avg_dependencies': 0
            }

        completed_tasks = [t for t in task_history if t.get('completed_at')]
        total_tasks = len(task_history)
        
        # Calculate metrics
        completion_times = []
        priorities = {}
        types = {}
        dependency_counts = []

        for task in completed_tasks:
            # Completion time
            if isinstance(task.get('created_at'), (int, float)) and isinstance(task.get('completed_at'), (int, float)):
                completion_time = (task['completed_at'] - task['created_at']) / 3600.0  # Convert to hours
                completion_times.append(completion_time)

            # Priority statistics
            priority = task.get('priority', 'unknown')
            priorities[priority] = priorities.get(priority, 0) + 1

            # Type statistics
            task_type = task.get('type', 'unknown')
            types[task_type] = types.get(task_type, 0) + 1

            # Dependency statistics
            deps = len(task.get('depends_on', []))
            dependency_counts.append(deps)

        return {
            'avg_completion_time_hours': sum(completion_times) / len(completion_times) if completion_times else None,
            'success_rate': len(completed_tasks) / total_tasks if total_tasks > 0 else 0,
            'completion_by_priority': priorities,
            'completion_by_type': types,
            'avg_dependencies': sum(dependency_counts) / len(dependency_counts) if dependency_counts else 0
        }


# ---------------------------------------------------------------------------
# CLASS: GitHubActivityMonitor
# ---------------------------------------------------------------------------
class GitHubActivityMonitor:
    """
    A stub for monitoring GitHub commits, PRs, or issues related to specific tasks.
    """

    def __init__(self, github_token: str, repo_name: str):
        self.github = Github(github_token)
        self.repo = self.github.get_repo(repo_name)

    def get_repository_contents(self, path: str = "") -> dict:
        """
        Recursively get contents of repository or specific path
        Returns a dictionary with both file structure and contents
        """
        contents = {
            "structure": {},
            "file_contents": {}
        }
        try:
            for content in self.repo.get_contents(path):
                if content.type == "dir":
                    # Recursively get contents of subdirectories
                    sub_contents = self.get_repository_contents(content.path)
                    contents["structure"][content.name] = sub_contents["structure"]
                    contents["file_contents"].update(sub_contents["file_contents"])
                else:
                    # Store both the file info and its contents
                    if content.name.endswith(('.txt', '.json')):
                        try:
                            file_content = content.decoded_content.decode()
                            contents["structure"][content.name] = "file"
                            contents["file_contents"][content.path] = {
                                "content": file_content,
                                "type": content.name.split('.')[-1]
                            }
                            print(f"Successfully loaded: {content.path}")
                        except Exception as e:
                            print(f"Error loading {content.path}: {e}")
        except Exception as e:
            print(f"Error accessing {path}: {e}")
        return contents

    def get_tasks(self) -> dict:
        """
        Get task files from repository
        Returns both the tasks and their raw content
        """
        tasks_content = {
            "tasks": [],
            "raw_contents": {}
        }
        try:
            repo_contents = self.get_repository_contents(TASKS_PATH)
            
            # Process all JSON and text files found
            for file_path, file_info in repo_contents["file_contents"].items():
                print(f"Processing file: {file_path}")
                
                if file_info["type"] == "json":
                    try:
                        content = json.loads(file_info["content"])
                        if isinstance(content, dict) and "tasks" in content:
                            tasks_content["tasks"].extend(content["tasks"])
                        tasks_content["raw_contents"][file_path] = content
                    except json.JSONDecodeError as e:
                        print(f"Error parsing JSON from {file_path}: {e}")
                else:  # txt files
                    tasks_content["raw_contents"][file_path] = file_info["content"]
                    
            print(f"Found {len(tasks_content['tasks'])} tasks across {len(tasks_content['raw_contents'])} files")
            
        except Exception as e:
            print(f"Error getting tasks: {e}")
        return tasks_content

    def track_related_activity(self, task_id, timeframe):
        """
        Monitor GitHub for activity related to tasks
        """
        events = []
        try:
            # Get commits mentioning task_id
            commits = self.repo.get_commits(since=timeframe)
            for commit in commits:
                if str(task_id) in commit.commit.message:
                    events.append({
                        "task_id": task_id,
                        "timestamp": commit.commit.author.date.timestamp(),
                        "activity_type": "commit",
                        "message": commit.commit.message
                    })
            
            # Get issues/PRs mentioning task_id
            issues = self.repo.get_issues(state='all', since=timeframe)
            for issue in issues:
                if str(task_id) in issue.title or str(task_id) in issue.body:
                    events.append({
                        "task_id": task_id,
                        "timestamp": issue.created_at.timestamp(),
                        "activity_type": "issue",
                        "message": issue.title
                    })
        except Exception as e:
            print(f"Error tracking GitHub activity: {e}")
        return events


# ---------------------------------------------------------------------------
# CLASS: UserInteractionTracker
# ---------------------------------------------------------------------------
class UserInteractionTracker:
    """
    Tracks how users interact with suggestions (did they accept, modify, reject them, etc.)
    """

    def __init__(self):
        self.interactions_log = []

    def track_interactions(self, suggestion_id, action="viewed", metadata=None):
        """
        Track user interactions with suggestions
        
        :param suggestion_id: The ID of the suggestion
        :param action: One of ['viewed', 'accepted', 'modified', 'rejected']
        :param metadata: Optional dict with additional interaction data
        :return: dict of the tracked interaction
        """
        interaction_event = {
            "suggestion_id": suggestion_id,
            "timestamp": time.time(),
            "action": action,
            "metadata": metadata or {}
        }
        self.interactions_log.append(interaction_event)
        
        # Store interaction in feedback repository
        try:
            feedback_repo = FeedbackRepository(GITHUB_TOKEN, GITHUB_REPO)
            feedback_repo.update_outcomes(interaction_event)
            print(f"[INFO] Stored interaction for suggestion {suggestion_id}")
        except Exception as e:
            print(f"[ERROR] Failed to store interaction: {e}")
            
        return interaction_event


# ---------------------------------------------------------------------------
# CLASS: TemporalAnalysis
# ---------------------------------------------------------------------------
class TemporalAnalysis:
    """
    Analyzes time-based patterns that affect success, e.g. time of day or day of week
    that tasks are more successfully completed.
    """

    def analyze_patterns(self, historical_data):
        """Evaluate time-based patterns in the historical_data."""
        if not historical_data:
            return {
                "completions_by_hour": [0] * 24,
                "completions_by_day": [0] * 7,
                "avg_completion_time_by_hour": [0] * 24,
                "success_rate_by_day": [0] * 7
            }

        completions_by_hour = [0] * 24
        completions_by_day = [0] * 7
        completion_times_by_hour = [[] for _ in range(24)]
        attempts_by_day = [0] * 7
        successes_by_day = [0] * 7

        for item in historical_data:
            created_at = item.get('created_at')
            completed_at = item.get('completed_at')
            
            if isinstance(completed_at, (int, float)):
                completion_dt = datetime.fromtimestamp(completed_at)
                completions_by_hour[completion_dt.hour] += 1
                completions_by_day[completion_dt.weekday()] += 1

                if isinstance(created_at, (int, float)):
                    completion_time = (completed_at - created_at) / 3600  # hours
                    completion_times_by_hour[completion_dt.hour].append(completion_time)

            if isinstance(created_at, (int, float)):
                created_dt = datetime.fromtimestamp(created_at)
                attempts_by_day[created_dt.weekday()] += 1
                if completed_at:
                    successes_by_day[created_dt.weekday()] += 1

        # Calculate average completion time by hour
        avg_completion_time_by_hour = [
            sum(times) / len(times) if times else 0 
            for times in completion_times_by_hour
        ]

        # Calculate success rate by day
        success_rate_by_day = [
            successes_by_day[i] / attempts_by_day[i] if attempts_by_day[i] > 0 else 0
            for i in range(7)
        ]

        return {
            "completions_by_hour": completions_by_hour,
            "completions_by_day": completions_by_day,
            "avg_completion_time_by_hour": avg_completion_time_by_hour,
            "success_rate_by_day": success_rate_by_day
        }


# ---------------------------------------------------------------------------
# CLASS: FeedbackCollector
# ---------------------------------------------------------------------------
class FeedbackCollector:
    """
    Collects feedback from tasks, GitHub activity, user interactions, and temporal data.
    """

    def __init__(self, github_monitor: GitHubActivityMonitor, user_tracker: UserInteractionTracker, temporal_analyzer: TemporalAnalysis):
        self.github_monitor = github_monitor
        self.user_tracker = user_tracker
        self.temporal_analyzer = temporal_analyzer

    def collect_feedback(self, suggestion_id):
        """
        Aggregate feedback from all sources for a given suggestion.

        :param suggestion_id: the ID for which feedback is being collected
        :return: dict containing combined feedback info
        """
        # In a real scenario, we might gather relevant tasks, call track_related_activity, etc.
        # Here, we'll do a simple demonstration:
        interactions = self.user_tracker.track_interactions(suggestion_id)
        feedback_data = {
            "suggestion_id": suggestion_id,
            "user_interactions": interactions,
            "collected_at": time.time()
        }
        return feedback_data

    def aggregate_feedback(self, *feedback_sources):
        """
        Combine feedback from multiple sources into a single structured dict.

        :param feedback_sources: One or more dicts of feedback
        :return: A single merged dict
        """
        merged = {}
        for source in feedback_sources:
            merged.update(source)
        return merged


# ---------------------------------------------------------------------------
# CLASS: FeedbackRepository
# ---------------------------------------------------------------------------
class FeedbackRepository:
    """
    Reads and writes various feedback JSON/JSONL files (action_outcomes, success_patterns, temporal_analysis)
    """

    def __init__(self, github_token: str, repo_name: str):
        self.github = Github(github_token)
        self.repo = self.github.get_repo(repo_name)

    def ensure_file_exists(self, path: str, initial_content: str = ""):
        """Create file if it doesn't exist"""
        try:
            self.repo.get_contents(path)
            print(f"File exists: {path}")
        except Exception as e:
            if "404" in str(e):
                print(f"Creating new file: {path}")
                try:
                    self.repo.create_file(
                        path=path,
                        message=f"Initialize {path}",
                        content=initial_content
                    )
                except Exception as create_error:
                    print(f"Error creating file {path}: {create_error}")
            else:
                print(f"Error checking {path}: {e}")

    def update_outcomes(self, feedback_data):
        """Update feedback data in GitHub repo"""
        try:
            path = f"{FEEDBACK_DIR}/action_outcomes.jsonl"
            
            # Ensure feedback directory and file exist
            self.ensure_file_exists(path, "")
            
            # Get current content
            content = self.repo.get_contents(path)
            
            # Append new data
            current_content = content.decoded_content.decode() if content else ""
            updated_content = current_content + "\n" + json.dumps(feedback_data) if current_content else json.dumps(feedback_data)
            
            self.repo.update_file(
                path=path,
                message="Update action outcomes",
                content=updated_content,
                sha=content.sha
            )
            print(f"Successfully updated {path}")
        except Exception as e:
            print(f"Error updating outcomes: {e}")

    def update_patterns(self, new_patterns):
        """Update success patterns file"""
        try:
            path = f"{FEEDBACK_DIR}/success_patterns.json"
            
            # Ensure file exists
            self.ensure_file_exists(path, "{}")
            
            content = self.repo.get_contents(path)
            existing = json.loads(content.decoded_content.decode()) if content else {}
            existing.update(new_patterns)
            
            self.repo.update_file(
                path=path,
                message="Update success patterns",
                content=json.dumps(existing, indent=2),
                sha=content.sha
            )
            print(f"Successfully updated {path}")
        except Exception as e:
            print(f"Error updating patterns: {e}")

    def update_temporal(self, temporal_data):
        """Update temporal analysis file"""
        try:
            path = f"{FEEDBACK_DIR}/temporal_analysis.json"
            
            # Ensure file exists
            self.ensure_file_exists(path, "{}")
            
            content = self.repo.get_contents(path)
            existing = json.loads(content.decoded_content.decode()) if content else {}
            existing.update(temporal_data)
            
            self.repo.update_file(
                path=path,
                message="Update temporal analysis",
                content=json.dumps(existing, indent=2),
                sha=content.sha
            )
            print(f"Successfully updated {path}")
        except Exception as e:
            print(f"Error updating temporal: {e}")


# ---------------------------------------------------------------------------
# CLASS: ContextAggregator
# ---------------------------------------------------------------------------
class ContextAggregator:
    """
    Aggregates all relevant feedback data with the user query and repository info to
    produce a final context for the LLM.
    """

    def aggregate_context(self, query, repo_contents):
        """
        Collect relevant feedback data and combine it with user query + repo info
        """
        print("\n=== Files and Content Being Aggregated ===")
        print("Repository Contents Structure:")
        
        # Pretty print the structure
        if isinstance(repo_contents, dict):
            for key, value in repo_contents.items():
                if key == "raw_contents":
                    print("\nRaw Contents Files:")
                    for file_path in value.keys():
                        print(f"- {file_path}")
                elif key == "tasks":
                    print(f"\nTasks Found: {len(value)}")
                else:
                    print(f"- {key}")

        context = {
            "user_query": query,
            "repo_info": repo_contents,
        }
        
        print("\n=== Final Aggregated Context ===")
        print(json.dumps(context, indent=2))
        return context

    def get_feedback_context(self):
        """
        Load outcomes, patterns, temporal data and tasks from the repo
        """
        print("\n=== Loading Repository Contents ===")
        
        github_monitor = GitHubActivityMonitor(GITHUB_TOKEN, GITHUB_REPO)
        tasks_content = github_monitor.get_tasks()
        
        print("\n=== Loading Feedback Files ===")
        
        # Load existing feedback data
        outcomes = []
        if os.path.exists(ACTION_OUTCOMES_FILE):
            print(f"Loading: {ACTION_OUTCOMES_FILE}")
            with jsonlines.open(ACTION_OUTCOMES_FILE, mode='r') as reader:
                outcomes = list(reader)
        else:
            print(f"File not found: {ACTION_OUTCOMES_FILE}")

        success_patterns = {}
        if os.path.exists(SUCCESS_PATTERNS_FILE):
            print(f"Loading: {SUCCESS_PATTERNS_FILE}")
            with open(SUCCESS_PATTERNS_FILE, 'r', encoding='utf-8') as f:
                success_patterns = json.load(f)
        else:
            print(f"File not found: {SUCCESS_PATTERNS_FILE}")

        temporal_analysis = {}
        if os.path.exists(TEMPORAL_ANALYSIS_FILE):
            print(f"Loading: {TEMPORAL_ANALYSIS_FILE}")
            with open(TEMPORAL_ANALYSIS_FILE, 'r', encoding='utf-8') as f:
                temporal_analysis = json.load(f)
        else:
            print(f"File not found: {TEMPORAL_ANALYSIS_FILE}")

        # Combine all context
        feedback_context = {
            "tasks_content": tasks_content,
            "action_outcomes": outcomes,
            "success_patterns": success_patterns,
            "temporal_analysis": temporal_analysis
        }
        
        print("\n=== Feedback Context Summary ===")
        print(json.dumps(feedback_context, indent=2))
        return feedback_context


# ---------------------------------------------------------------------------
# CLASS: LLMService (Gemini integration)
# ---------------------------------------------------------------------------
class LLMService:
    """
    Sends prompts to Google's Gemini model, retrieves suggestions, and parses them.
    """

    def __init__(self, api_key=os.getenv('GOOGLE_API_KEY')):
        self.api_key = api_key
        self.gemini_endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent?key="
            + self.api_key
        )

    def generate_suggestion(self, context):
        """
        Construct a prompt from the context, call Gemini, and return an actionable suggestion.
        """
        user_query = context.get("user_query", "No user query provided.")
        
        prompt_text = f"""
User Query: {user_query}

Repository Context:
{json.dumps(context.get('repo_info', {}), indent=2)}

Please analyze the above context and suggest next best actions.
"""

        print("\n=== Final Prompt Being Sent to LLM ===")
        print(prompt_text)

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text}
                    ]
                }
            ]
        }

        print("\n=== API Payload Size ===")
        payload_size = len(json.dumps(payload))
        print(f"Payload size: {payload_size} characters")

        try:
            response = requests.post(
                self.gemini_endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=60  # Keep longer timeout just in case
            )
            response.raise_for_status()
            data = response.json()
            
            print("\n=== API Response ===")
            print(json.dumps(data, indent=2))
            
            suggestions = data.get("candidates", [{}])
            if suggestions:
                text_suggestion = suggestions[0].get("content", "No suggestion content found.")
            else:
                text_suggestion = "No suggestions returned from Gemini."
        except requests.exceptions.Timeout:
            print("\n[ERROR] API request timed out.")
            text_suggestion = "Error: API request timed out."
        except requests.exceptions.RequestException as e:
            print(f"\n[ERROR] API request failed: {str(e)}")
            text_suggestion = f"Error contacting LLM: {str(e)}"
        except Exception as e:
            print(f"\n[ERROR] Unexpected error: {str(e)}")
            text_suggestion = f"Unexpected error: {str(e)}"

        return text_suggestion


# ---------------------------------------------------------------------------
# CLASS: ActionSuggestionSystem
# ---------------------------------------------------------------------------
class ActionSuggestionSystem:
    """
    Main application class for processing user queries (multiple projects) and updating feedback.
    """

    def __init__(self,
                 llm_service: LLMService,
                 context_aggregator: ContextAggregator,
                 feedback_repo: FeedbackRepository,
                 feedback_collector: FeedbackCollector,
                 task_analyzer: TaskAnalysisService,
                 user_tracker: UserInteractionTracker,
                 temporal_analyzer: TemporalAnalysis):
        self.llm_service = llm_service
        self.context_aggregator = context_aggregator
        self.feedback_repo = feedback_repo
        self.feedback_collector = feedback_collector
        self.task_analyzer = task_analyzer
        self.user_tracker = user_tracker
        self.temporal_analyzer = temporal_analyzer

    def process_query(self, user_query):
        """
        Pull latest data, aggregate context, and generate suggestion from LLM.
        """
        # 1. Get feedback context from the GitHub repository
        feedback_context = self.context_aggregator.get_feedback_context()

        # 2. Combine the user_query + feedback context
        final_context = self.context_aggregator.aggregate_context(
            user_query,
            repo_contents=feedback_context
        )

        # 3. Generate suggestion using LLM
        suggestion_text = self.llm_service.generate_suggestion(final_context)

        # 4. Track the suggestion
        suggestion_id = f"suggestion-{int(time.time())}"
        self.user_tracker.track_interactions(suggestion_id)

        return suggestion_text

    def update_feedback(self):
        """
        Periodic feedback update process
        """
        try:
            # Get current timestamp for this feedback cycle
            current_time = int(time.time())
            
            # Collect feedback from multiple sources
            task_analyzer_feedback = self.task_analyzer.analyze_completion_patterns(
                self.context_aggregator.get_feedback_context().get("tasks_content", {}).get("tasks", [])
            )
            
            temporal_feedback = self.temporal_analyzer.analyze_patterns(
                self.context_aggregator.get_feedback_context().get("tasks_content", {}).get("tasks", [])
            )
            
            # Aggregate all feedback
            aggregated_feedback = {
                "timestamp": current_time,
                "task_patterns": task_analyzer_feedback,
                "temporal_patterns": temporal_feedback,
                "user_interactions": self.user_tracker.interactions_log
            }

            # Store in repository
            self.feedback_repo.update_patterns(task_analyzer_feedback)
            self.feedback_repo.update_temporal(temporal_feedback)
            self.feedback_repo.update_outcomes(aggregated_feedback)
            
            print("[INFO] Successfully updated feedback in repository")
            
        except Exception as e:
            print(f"[ERROR] Failed to update feedback: {e}")


# ---------------------------------------------------------------------------
# Cron-like Jobs
# ---------------------------------------------------------------------------
def hourly_task_sync(task_analyzer: TaskAnalysisService):
    """
    Sync tasks and run feedback updates
    """
    try:
        # Get current tasks from GitHub
        github_monitor = GitHubActivityMonitor(GITHUB_TOKEN, GITHUB_REPO)
        current_tasks = github_monitor.get_tasks().get("tasks", [])
        
        # Load previous tasks from feedback repository
        feedback_repo = FeedbackRepository(GITHUB_TOKEN, GITHUB_REPO)
        previous_tasks_content = feedback_repo.repo.get_contents(f"{FEEDBACK_DIR}/previous_tasks.json")
        previous_tasks = json.loads(previous_tasks_content.decoded_content.decode()) if previous_tasks_content else []
        
        # Analyze changes
        changes = task_analyzer.monitor_task_changes(previous_tasks, current_tasks)
        
        # Store current tasks as previous for next sync
        feedback_repo.update_file(
            f"{FEEDBACK_DIR}/previous_tasks.json",
            "Update previous tasks",
            json.dumps(current_tasks, indent=2)
        )
        
        # Store changes in feedback
        feedback_repo.update_outcomes({
            "timestamp": time.time(),
            "type": "task_sync",
            "changes": changes
        })
        
        print(f"[INFO] Task sync completed. Changes detected: {changes}")
        
    except Exception as e:
        print(f"[ERROR] Task sync failed: {e}")


def daily_pattern_analysis(task_analyzer: TaskAnalysisService,
                         temporal_analyzer: TemporalAnalysis,
                         feedback_repo: FeedbackRepository):
    """
    Perform daily analysis of patterns
    """
    try:
        # Get all tasks
        github_monitor = GitHubActivityMonitor(GITHUB_TOKEN, GITHUB_REPO)
        task_history = github_monitor.get_tasks().get("tasks", [])
        
        # Analyze patterns
        completion_stats = task_analyzer.analyze_completion_patterns(task_history)
        temporal_data = temporal_analyzer.analyze_patterns(task_history)
        
        # Store analysis results
        analysis_results = {
            "timestamp": time.time(),
            "completion_patterns": completion_stats,
            "temporal_patterns": temporal_data
        }
        
        feedback_repo.update_patterns(completion_stats)
        feedback_repo.update_temporal(temporal_data)
        
        print("[INFO] Daily pattern analysis completed successfully")
        
    except Exception as e:
        print(f"[ERROR] Daily pattern analysis failed: {e}")


# ---------------------------------------------------------------------------
# Main executable (example usage)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Get API key from environment
    gemini_api_key = os.getenv('GOOGLE_API_KEY')
    if not gemini_api_key:
        raise ValueError("GOOGLE_API_KEY environment variable is not set")

    # Instantiate all components
    github_monitor = GitHubActivityMonitor(GITHUB_TOKEN, GITHUB_REPO)
    user_tracker = UserInteractionTracker()
    temporal_analyzer = TemporalAnalysis()
    feedback_collector = FeedbackCollector(github_monitor, user_tracker, temporal_analyzer)
    feedback_repo = FeedbackRepository(GITHUB_TOKEN, GITHUB_REPO)
    context_aggregator = ContextAggregator()
    llm_service = LLMService(api_key=gemini_api_key)  # Use environment variable instead of placeholder

    task_analyzer = TaskAnalysisService()

    action_suggestion_system = ActionSuggestionSystem(
        llm_service=llm_service,
        context_aggregator=context_aggregator,
        feedback_repo=feedback_repo,
        feedback_collector=feedback_collector,
        task_analyzer=task_analyzer,
        user_tracker=user_tracker,
        temporal_analyzer=temporal_analyzer
    )

    # Example: Hourly task sync
    hourly_task_sync(task_analyzer)

    # Example: Daily pattern analysis
    daily_pattern_analysis(task_analyzer, temporal_analyzer, feedback_repo)

    # Example: Process user query
    user_query = "What tasks should I focus on next for Project X?"
    suggestion = action_suggestion_system.process_query(user_query)
    print("[INFO] LLM Suggestion:", suggestion)

    # Example: Periodic feedback update
    action_suggestion_system.update_feedback()

    print("[INFO] Script execution complete.")