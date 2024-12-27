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
from datetime import datetime, timedelta
from github import Github
from github.Repository import Repository
from dotenv import load_dotenv
from functools import lru_cache
from typing import Optional, Dict, List
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
# Cache and State Management
# ---------------------------------------------------------------------------
class StateManager:
    """Manages application state and caching"""
    def __init__(self):
        self._cache = {}
        self._last_update = {}
        self._rate_limit_remaining = None
        self._rate_limit_reset = None

    def get_cache(self, key: str) -> Optional[dict]:
        """Get cached data if it exists and is not expired"""
        if key in self._cache and key in self._last_update:
            # Check if cache is still valid (1 hour for most data)
            if datetime.now() - self._last_update[key] < timedelta(hours=1):
                return self._cache[key]
        return None

    def set_cache(self, key: str, data: dict):
        """Set cache data with current timestamp"""
        self._cache[key] = data
        self._last_update[key] = datetime.now()

    def clear_cache(self, key: str = None):
        """Clear specific or all cache entries"""
        if key:
            self._cache.pop(key, None)
            self._last_update.pop(key, None)
        else:
            self._cache.clear()
            self._last_update.clear()

    def update_rate_limits(self, remaining: int, reset_time: datetime):
        """Update GitHub rate limit information"""
        self._rate_limit_remaining = remaining
        self._rate_limit_reset = reset_time

    def can_make_request(self) -> bool:
        """Check if we can make a GitHub request"""
        if self._rate_limit_remaining is None:
            return True
        if self._rate_limit_remaining <= 0:
            if datetime.now() < self._rate_limit_reset:
                return False
        return True

# Create global state manager
state_manager = StateManager()

# ---------------------------------------------------------------------------
# Resource Management
# ---------------------------------------------------------------------------
class GitHubResourceManager:
    """Manages GitHub API resources and rate limits"""
    def __init__(self, github_token: str, repo_name: str):
        self.github = Github(github_token)
        self.repo = self.github.get_repo(repo_name)
        self._update_rate_limits()

    def _update_rate_limits(self):
        """Update rate limit information"""
        rate_limit = self.github.get_rate_limit()
        state_manager.update_rate_limits(
            rate_limit.core.remaining,
            rate_limit.core.reset
        )

    @lru_cache(maxsize=128)
    def get_repository_contents(self, path: str = "") -> dict:
        """Cached repository contents retrieval"""
        if not state_manager.can_make_request():
            raise Exception("GitHub API rate limit exceeded")
        
        try:
            contents = self.repo.get_contents(path)
            self._update_rate_limits()
            return contents
        except Exception as e:
            logger.error(f"Error accessing repository contents: {e}")
            raise

# ---------------------------------------------------------------------------
# Updated GitHubActivityMonitor
# ---------------------------------------------------------------------------
class GitHubActivityMonitor:
    """Monitors GitHub activity with proper caching and resource management"""
    def __init__(self, resource_manager: GitHubResourceManager):
        self.resource_manager = resource_manager

    def get_tasks(self) -> dict:
        """Get tasks with caching"""
        cached_tasks = state_manager.get_cache('tasks')
        if cached_tasks:
            return cached_tasks

        try:
            tasks_content = {
                "tasks": [],
                "raw_contents": {}
            }
            
            contents = self.resource_manager.get_repository_contents(TASKS_PATH)
            for content in contents:
                if content.name.endswith(('.json', '.txt')):
                    file_content = content.decoded_content.decode()
                    if content.name.endswith('.json'):
                        try:
                            parsed = json.loads(file_content)
                            if isinstance(parsed, dict) and "tasks" in parsed:
                                tasks_content["tasks"].extend(parsed["tasks"])
                            tasks_content["raw_contents"][content.path] = parsed
                        except json.JSONDecodeError as e:
                            logger.error(f"Error parsing JSON from {content.path}: {e}")
                    else:
                        tasks_content["raw_contents"][content.path] = file_content

            state_manager.set_cache('tasks', tasks_content)
            return tasks_content
        except Exception as e:
            logger.error(f"Error getting tasks: {e}")
            raise

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
    def __init__(self, github_monitor: GitHubActivityMonitor):
        self.github_monitor = github_monitor
        
    def get_feedback_context(self):
        """Get feedback context with caching"""
        cached_context = state_manager.get_cache('feedback_context')
        if cached_context:
            return cached_context

        try:
            tasks_content = self.github_monitor.get_tasks()
            feedback_context = {
                "tasks_content": tasks_content,
                "action_outcomes": self._load_outcomes(),
                "success_patterns": self._load_patterns(),
                "temporal_analysis": self._load_temporal()
            }
            
            state_manager.set_cache('feedback_context', feedback_context)
            return feedback_context
        except Exception as e:
            logger.error(f"Error getting feedback context: {e}")
            raise

    def _load_outcomes(self):
        """Load outcomes with error handling"""
        try:
            return self._load_json_file(ACTION_OUTCOMES_FILE)
        except Exception as e:
            logger.error(f"Error loading outcomes: {e}")
            return []

    def _load_patterns(self):
        """Load patterns with error handling"""
        try:
            return self._load_json_file(SUCCESS_PATTERNS_FILE)
        except Exception as e:
            logger.error(f"Error loading patterns: {e}")
            return {}

    def _load_temporal(self):
        """Load temporal analysis with error handling"""
        try:
            return self._load_json_file(TEMPORAL_ANALYSIS_FILE)
        except Exception as e:
            logger.error(f"Error loading temporal analysis: {e}")
            return {}

    @staticmethod
    def _load_json_file(filepath):
        """Load JSON file with proper error handling"""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            logger.warning(f"File not found: {filepath}")
            return {} if filepath.endswith('.json') else []
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing JSON from {filepath}: {e}")
            return {} if filepath.endswith('.json') else []


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
# CLASS: TaskAnalysisService
# ---------------------------------------------------------------------------
class TaskAnalysisService:
    """Analyzes task completion patterns and dependencies"""
    
    def monitor_task_changes(self, previous_tasks: List[dict], current_tasks: List[dict]) -> dict:
        """Compare task lists to identify completed and modified tasks."""
        previous_ids = {task['id'] for task in previous_tasks}
        current_ids = {task['id'] for task in current_tasks}

        completed = previous_ids - current_ids
        modified = []
        downstream = []

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

    def analyze_completion_patterns(self, task_history: List[dict]) -> dict:
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
# Main initialization
# ---------------------------------------------------------------------------
def initialize_system():
    """Initialize system with proper resource management"""
    try:
        resource_manager = GitHubResourceManager(GITHUB_TOKEN, GITHUB_REPO)
        github_monitor = GitHubActivityMonitor(resource_manager)
        context_aggregator = ContextAggregator(github_monitor)
        
        # Initialize other components...
        
        return ActionSuggestionSystem(
            llm_service=LLMService(),
            context_aggregator=context_aggregator,
            feedback_repo=FeedbackRepository(GITHUB_TOKEN, GITHUB_REPO),
            feedback_collector=FeedbackCollector(github_monitor, UserInteractionTracker(), TemporalAnalysis()),
            task_analyzer=TaskAnalysisService(),
            user_tracker=UserInteractionTracker(),
            temporal_analyzer=TemporalAnalysis()
        )
    except Exception as e:
        logger.error(f"Error initializing system: {e}")
        raise


# ---------------------------------------------------------------------------
# Main executable (example usage)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        system = initialize_system()
        # Your main execution code here...
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)