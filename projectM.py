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

# ---------------------------------------------------------------------------
# Utility function to commit to GitHub (placeholder)
# ---------------------------------------------------------------------------
def commit_to_github(file_path, commit_message="Updating data file"):
    """
    Placeholder for committing changes to a GitHub repository.
    In production, you'd integrate with the GitHub API or run `git` commands.
    """
    # For demo, we simply print out that we're "committing" the file.
    print(f"[INFO] Committing {file_path} to GitHub with message: '{commit_message}'")
    # Insert real commit logic here (GitHub API calls or local git commands).


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
GITHUB_TOKEN = "YOUR_GITHUB_TOKEN"
GITHUB_REPO = "owner/repository"  # e.g. "username/project-repo"
TASKS_PATH = "tasks"  # Directory in repo containing task files
FEEDBACK_DIR = "feedback"  # Directory for feedback data


# ---------------------------------------------------------------------------
# CLASS: TaskAnalysisService
# ---------------------------------------------------------------------------
class TaskAnalysisService:
    """
    Compares previous tasks to current tasks to identify completed, modified, or
    newly created tasks. Also can analyze how completion might affect downstream tasks.
    """

    def monitor_task_changes(self, previous_tasks, current_tasks):
        """
        Compare task lists to identify completed and modified tasks.
        Return a dict with 'completed', 'modified', and 'downstream' tasks.

        :param previous_tasks: List of task dicts representing the old state.
        :param current_tasks: List of task dicts representing the new state.
        :return: dict with keys ['completed', 'modified', 'downstream'] containing lists of task IDs
        """
        previous_ids = {task['id'] for task in previous_tasks}
        current_ids = {task['id'] for task in current_tasks}

        completed = previous_ids - current_ids
        # For demonstration, let's treat anything that changed in description as "modified".
        modified = []
        for ctask in current_tasks:
            for ptask in previous_tasks:
                if ctask['id'] == ptask['id'] and ctask.get('description') != ptask.get('description'):
                    modified.append(ctask['id'])

        # Downstream tasks could be tasks that depend on completed tasks, but for brevity
        # we’ll just say any task that starts after a completed task is "downstream."
        # (Replace with actual logic in real scenarios)
        downstream = []
        for ctask in current_tasks:
            if 'depends_on' in ctask:
                # If any depends_on is in 'completed', consider this a downstream task
                if any(dep_id in completed for dep_id in ctask['depends_on']):
                    downstream.append(ctask['id'])

        return {
            'completed': list(completed),
            'modified': modified,
            'downstream': downstream
        }

    def analyze_completion_patterns(self, task_history):
        """
        Analyze patterns in task completion (e.g., average time to complete tasks,
        success rate, etc.)

        :param task_history: List of dicts representing historical tasks with completion info.
        :return: A dict of analytics, e.g., { 'avg_completion_time':..., 'success_rate':... }
        """
        # Example: compute average completion time (in hours) for tasks that have a 'completed_at'
        completed_tasks = [t for t in task_history if t.get('completed_at')]
        if not completed_tasks:
            avg_completion_time = None
            success_rate = 0
        else:
            total_time = 0
            success_count = len(completed_tasks)
            for ct in completed_tasks:
                start = ct.get('created_at')
                end = ct.get('completed_at')
                if isinstance(start, float) and isinstance(end, float):
                    total_time += (end - start)
            avg_completion_time = (total_time / success_count) / 3600.0 if success_count > 0 else None
            # Let success_rate be the fraction of tasks completed (assuming total tasks known).
            success_rate = success_count / len(task_history)

        return {
            'avg_completion_time_hours': avg_completion_time,
            'success_rate': success_rate
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
        """
        contents = {}
        try:
            for content in self.repo.get_contents(path):
                if content.type == "dir":
                    contents[content.name] = self.get_repository_contents(content.path)
                else:
                    contents[content.name] = content.decoded_content.decode()
        except Exception as e:
            print(f"Error getting repo contents: {e}")
        return contents

    def get_tasks(self) -> list:
        """
        Get task files from repository
        """
        tasks = []
        try:
            task_files = self.repo.get_contents(TASKS_PATH)
            for file in task_files:
                if file.name.endswith('.json'):
                    content = json.loads(file.decoded_content.decode())
                    tasks.extend(content.get('tasks', []))
        except Exception as e:
            print(f"Error getting tasks: {e}")
        return tasks

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
        # Could store interactions in memory or load from persistent storage
        self.interactions_log = []

    def track_interactions(self, suggestion_id):
        """
        In a real system, track if user accepted/rejected or how user responded to the suggestion.

        :param suggestion_id: The ID of the suggestion for which we track user interactions
        :return: dict of the tracked interaction
        """
        # Placeholder: let's assume user always "viewed" the suggestion
        interaction_event = {
            "suggestion_id": suggestion_id,
            "timestamp": time.time(),
            "action": "viewed"  # could be 'accepted', 'modified', 'rejected', etc.
        }
        self.interactions_log.append(interaction_event)
        print(f"[INFO] UserInteractionTracker: Recorded interaction for suggestion {suggestion_id}")
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
        """
        Evaluate time-based patterns in the historical_data.

        :param historical_data: list of feedback or task completion events with timestamps
        :return: dict capturing aggregated patterns
        """
        # For simplicity, let's just count completions by hour of day
        completions_by_hour = [0] * 24

        for item in historical_data:
            completed_at = item.get('completed_at')
            if isinstance(completed_at, float):
                # Convert epoch to hour of day
                hour_of_day = datetime.utcfromtimestamp(completed_at).hour
                completions_by_hour[hour_of_day] += 1

        # We can store these as a pattern
        return {"completions_by_hour": completions_by_hour}


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

    def update_outcomes(self, feedback_data):
        """Update feedback data in GitHub repo"""
        try:
            path = f"{FEEDBACK_DIR}/action_outcomes.jsonl"
            content = self.repo.get_contents(path)
            
            # Append new data
            updated_content = content.decoded_content.decode() + "\n" + json.dumps(feedback_data)
            
            self.repo.update_file(
                path=path,
                message="Update action outcomes",
                content=updated_content,
                sha=content.sha
            )
        except Exception as e:
            print(f"Error updating outcomes: {e}")

    def update_patterns(self, new_patterns):
        """
        Update success_patterns.json with new analysis
        :param new_patterns: dict with pattern data
        """
        existing = {}
        if os.path.exists(SUCCESS_PATTERNS_FILE):
            with open(SUCCESS_PATTERNS_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)

        existing.update(new_patterns)

        with open(SUCCESS_PATTERNS_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2)

        commit_to_github(SUCCESS_PATTERNS_FILE, "Updated success patterns")

    def update_temporal(self, temporal_data):
        """
        Update temporal_analysis.json with new patterns
        :param temporal_data: dict with time-based analysis
        """
        existing = {}
        if os.path.exists(TEMPORAL_ANALYSIS_FILE):
            with open(TEMPORAL_ANALYSIS_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)

        existing.update(temporal_data)

        with open(TEMPORAL_ANALYSIS_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2)

        commit_to_github(TEMPORAL_ANALYSIS_FILE, "Updated temporal analysis")


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

        :param query: str user query
        :param repo_contents: dict or list representing content from the GitHub repo
        :return: dict containing the aggregated context
        """
        context = {
            "user_query": query,
            "repo_info": repo_contents,
        }
        return context

    def get_feedback_context(self):
        """
        Load outcomes, patterns, temporal data from JSON/JSONL in the repo.
        :return: dict with keys [action_outcomes, success_patterns, temporal_analysis]
        """
        # Load the action outcomes
        outcomes = []
        if os.path.exists(ACTION_OUTCOMES_FILE):
            with jsonlines.open(ACTION_OUTCOMES_FILE, mode='r') as reader:
                outcomes = list(reader)

        # Load success patterns
        success_patterns = {}
        if os.path.exists(SUCCESS_PATTERNS_FILE):
            with open(SUCCESS_PATTERNS_FILE, 'r', encoding='utf-8') as f:
                success_patterns = json.load(f)

        # Load temporal
        temporal_analysis = {}
        if os.path.exists(TEMPORAL_ANALYSIS_FILE):
            with open(TEMPORAL_ANALYSIS_FILE, 'r', encoding='utf-8') as f:
                temporal_analysis = json.load(f)

        return {
            "action_outcomes": outcomes,
            "success_patterns": success_patterns,
            "temporal_analysis": temporal_analysis
        }


# ---------------------------------------------------------------------------
# CLASS: LLMService (Gemini integration)
# ---------------------------------------------------------------------------
class LLMService:
    """
    Sends prompts to Google's Gemini model, retrieves suggestions, and parses them.
    """

    def __init__(self, api_key="YOUR_KEY_HERE"):
        self.api_key = api_key
        self.gemini_endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent?key="
            + self.api_key
        )

    def generate_suggestion(self, context):
        """
        Construct a prompt from the context, call Gemini, and return an actionable suggestion.

        :param context: dict containing user_query, repo_info, or other relevant data
        :return: str with the LLM's suggestion
        """
        # Construct a prompt from the aggregated context
        user_query = context.get("user_query", "No user query provided.")
        # Example of how you might incorporate additional data:
        # context["repo_info"] or context["feedback_data"], etc.

        # In a more sophisticated scenario, you'd build a more structured prompt
        # that includes relevant historical feedback or patterns.

        # For demonstration, let's keep it simple:
        prompt_text = f"User query: {user_query}\n\nContext data: {context.get('repo_info', {})}\n\nPlease suggest next best actions."

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt_text}
                    ]
                }
            ]
        }

        try:
            response = requests.post(
                self.gemini_endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            # The structure of the Gemini response may differ. Let's assume it returns
            # something like data["candidates"][0]["content"] or similar.
            # We'll use a placeholder path for demonstration:
            suggestions = data.get("candidates", [{}])
            if suggestions:
                text_suggestion = suggestions[0].get("content", "No suggestion content found.")
            else:
                text_suggestion = "No suggestions returned from Gemini."
        except Exception as e:
            print(f"[ERROR] Gemini API call failed: {e}")
            text_suggestion = "Error contacting LLM."

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

        :param user_query: str user query (e.g., "What should I do next for project ABC?")
        :return: str the LLM's suggestion
        """
        # 1. Get feedback context from the GitHub repository
        feedback_context = self.context_aggregator.get_feedback_context()

        # 2. Combine the user_query + feedback context in a single aggregator
        final_context = self.context_aggregator.aggregate_context(
            user_query,
            repo_contents=feedback_context
        )

        # 3. Call the LLM to generate a suggestion
        suggestion_text = self.llm_service.generate_suggestion(final_context)

        # 4. (Optional) Track the suggestion in user interactions
        #    We can generate a suggestion_id if needed, or use a timestamp-based ID
        suggestion_id = f"suggestion-{int(time.time())}"
        self.user_tracker.track_interactions(suggestion_id)

        return suggestion_text

    def update_feedback(self):
        """
        Periodic feedback update process. Collect feedback from multiple sources
        and store aggregated feedback in the GitHub repository.
        """
        # For demonstration, let's assume we are collecting feedback for a single suggestion ID.
        # In a real scenario, you might collect for multiple tasks or suggestions.
        suggestion_id = f"feedback-{int(time.time())}"
        raw_feedback = self.feedback_collector.collect_feedback(suggestion_id)
        aggregated_feedback = self.feedback_collector.aggregate_feedback(raw_feedback)

        # Write to action_outcomes.jsonl
        self.feedback_repo.update_outcomes(aggregated_feedback)


# ---------------------------------------------------------------------------
# Cron-like Jobs
# ---------------------------------------------------------------------------
def hourly_task_sync(task_analyzer: TaskAnalysisService):
    """
    Sync tasks, run feedback updates, etc.
    In a real environment, fetch tasks from a database or project management API,
    detect changes, store them, commit them to GitHub, etc.
    """
    print("[INFO] Running hourly task sync...")
    # example usage
    previous_tasks = [
        {"id": "task1", "description": "Old description", "created_at": time.time() - 7200},
        {"id": "task2", "description": "Some other old description", "created_at": time.time() - 3600}
    ]
    current_tasks = [
        {"id": "task2", "description": "Some other old description", "created_at": time.time() - 3600},
        {"id": "task3", "description": "Brand new task", "created_at": time.time()}
    ]

    changes = task_analyzer.monitor_task_changes(previous_tasks, current_tasks)
    print(f"[INFO] Detected changes: {changes}")
    # Store or act upon them as needed
    # (Placeholder) In real usage, you might commit a JSON or parse it to the GitHub repo.


def daily_pattern_analysis(task_analyzer: TaskAnalysisService,
                           temporal_analyzer: TemporalAnalysis,
                           feedback_repo: FeedbackRepository):
    """
    Perform daily analysis of patterns, update success_patterns.json and temporal_analysis.json
    """
    print("[INFO] Running daily pattern analysis...")
    # In a real environment, load your entire task history from a database
    # Here, we just simulate a small historical set
    task_history = [
        {
            "id": "task1",
            "description": "Completed example task",
            "created_at": time.time() - 86400 * 2,  # created 2 days ago
            "completed_at": time.time() - 86400 * 1.5  # completed 1.5 days ago
        },
        {
            "id": "task2",
            "description": "Another completed task",
            "created_at": time.time() - 3600,
            "completed_at": time.time() - 1800  # completed 30 min ago
        },
        {
            "id": "task3",
            "description": "Ongoing task",
            "created_at": time.time() - 1800
            # not completed
        }
    ]

    # Analyze patterns
    completion_stats = task_analyzer.analyze_completion_patterns(task_history)
    feedback_repo.update_patterns(completion_stats)

    # Analyze temporal patterns
    temporal_data = temporal_analyzer.analyze_patterns(task_history)
    feedback_repo.update_temporal(temporal_data)

    print("[INFO] Daily pattern analysis completed.")


# ---------------------------------------------------------------------------
# Main executable (example usage)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Instantiate all components
    github_monitor = GitHubActivityMonitor(GITHUB_TOKEN, GITHUB_REPO)
    user_tracker = UserInteractionTracker()
    temporal_analyzer = TemporalAnalysis()
    feedback_collector = FeedbackCollector(github_monitor, user_tracker, temporal_analyzer)
    feedback_repo = FeedbackRepository(GITHUB_TOKEN, GITHUB_REPO)
    context_aggregator = ContextAggregator()
    llm_service = LLMService(api_key="YOUR_KEY_HERE")  # replace with real key

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