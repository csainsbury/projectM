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
from typing import Optional, Dict, List, Any, Callable  # Add Callable here
import logging
import sys
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from time import sleep
from github import RateLimitExceededException
from werkzeug.utils import secure_filename
import threading
import hashlib
from apscheduler.schedulers.background import BackgroundScheduler
from types import SimpleNamespace

# After imports but before other classes
class GeminiRateLimiter:
    """Rate limiter for Gemini API calls"""
    def __init__(self):
        self.calls = []
        self.max_calls_per_minute = 60  # Adjust based on your API tier
        self.max_concurrent = 3
        self.current_concurrent = 0
        self._lock = threading.Lock()

    def can_make_request(self) -> bool:
        """Check if we can make a request based on rate limits"""
        now = time.time()
        
        # Clean up old calls
        self.calls = [t for t in self.calls if now - t < 60]
        
        with self._lock:
            if (len(self.calls) >= self.max_calls_per_minute or 
                self.current_concurrent >= self.max_concurrent):
                return False
            
            # Add this call
            self.calls.append(now)
            self.current_concurrent += 1
            return True

    def release_request(self):
        """Release a concurrent request slot"""
        with self._lock:
            self.current_concurrent = max(0, self.current_concurrent - 1)

    def wait_for_capacity(self, timeout: int = 30) -> bool:
        """Wait until capacity is available"""
        start = time.time()
        while time.time() - start < timeout:
            if self.can_make_request():
                return True
            time.sleep(1)
        return False

# Initialize Flask app
app = Flask(__name__, static_folder='static')
CORS(app, resources={r"/api/*": {"origins": "*"}})

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

GITHUB_REPO = os.getenv('GITHUB_REPO')
if not GITHUB_REPO:
    raise ValueError("GITHUB_REPO environment variable is not set")

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
        """Get all tasks from the repository"""
        try:
            logger.info("Starting task retrieval from GitHub")
            tasks = []
            
            # Read inbox tasks first
            inbox_file = os.path.join('tasks', 'inbox_tasks.json')
            if os.path.exists(inbox_file):
                with open(inbox_file, 'r') as f:
                    inbox_data = json.load(f)
                    if isinstance(inbox_data, dict) and 'tasks' in inbox_data:
                        tasks.extend(inbox_data['tasks'])
                        logger.info(f"Added {len(inbox_data['tasks'])} tasks from inbox")

            # Then read project files...
            tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
            
            # Get list of files, excluding directories and special files
            files = [f for f in os.listdir(tasks_dir) 
                    if os.path.isfile(os.path.join(tasks_dir, f)) and 
                    not f.startswith('.') and
                    f not in {'system_prompt.txt'} and
                    not f.startswith('reduced_') and
                    os.path.dirname(f) != 'uncompressed_context']
            
            logger.info(f"Found {len(files)} files in tasks directory")
            
            for filename in files:
                try:
                    file_path = os.path.join(tasks_dir, filename)
                    logger.info(f"Processing file: {filename}")
                    
                    if filename.endswith('.json'):
                        logger.info(f"Processing JSON file: {filename}")
                        with open(file_path, 'r') as f:
                            json_data = json.load(f)
                            if isinstance(json_data, list):
                                tasks.extend(json_data)
                                logger.info(f"Added {len(json_data)} tasks from {filename}")
                    
                    elif filename.endswith('.txt'):
                        logger.info(f"Processing TXT file: {filename}")
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            tasks.append({
                                'id': os.path.splitext(filename)[0],
                                'type': 'text',
                                'content': content,
                                'created_at': datetime.now().strftime('%c'),
                                'filename': file_path
                            })
                            logger.info(f"Added text task from {filename}")
                            
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {e}")
                    continue
            
            logger.info(f"Found {len(tasks)} total tasks")
            if tasks:
                logger.info(f"Task content sample: {tasks[:1]}")
            
            return {"tasks": tasks}
            
        except Exception as e:
            logger.error(f"Error retrieving tasks: {e}")
            return {"tasks": []}

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
    """Tracks and analyzes user interactions with tasks and suggestions"""

    def __init__(self):
        self.interactions_log = []

    def track_interactions(self, suggestion_id, action="viewed", metadata=None):
        """Track basic interaction events"""
        interaction_event = {
            "suggestion_id": suggestion_id,
            "timestamp": time.time(),
            "action": action,
            "metadata": metadata or {}
        }
        self.interactions_log.append(interaction_event)
        return interaction_event

    def analyze_interaction_patterns(self, interaction_history: List[dict]) -> dict:
        """Analyze detailed interaction patterns"""
        try:
            analysis = {
                'time_to_action': self._calculate_response_times(interaction_history),
                'modification_patterns': self._analyze_changes(interaction_history),
                'delegation_flow': self._track_task_assignments(interaction_history),
                'subtask_creation': self._analyze_task_breakdown(interaction_history)
            }
            logger.info(f"Analyzed interaction patterns: {len(interaction_history)} events")
            return analysis
        except Exception as e:
            logger.error(f"Error analyzing interaction patterns: {e}")
            return {}

    def _calculate_response_times(self, history: List[dict]) -> dict:
        """Calculate time between viewing and taking action"""
        try:
            response_times = {
                'view_to_accept': [],
                'view_to_reject': [],
                'view_to_modify': []
            }
            
            # Group by suggestion_id
            by_suggestion = {}
            for event in history:
                sid = event['suggestion_id']
                by_suggestion.setdefault(sid, []).append(event)
            
            # Calculate times for each suggestion
            for sid, events in by_suggestion.items():
                events.sort(key=lambda x: x['timestamp'])
                view_time = next((e['timestamp'] for e in events if e['action'] == 'viewed'), None)
                
                if view_time:
                    for event in events:
                        if event['timestamp'] > view_time:
                            key = f"view_to_{event['action']}"
                            if key in response_times:
                                response_times[key].append(event['timestamp'] - view_time)
            
            # Calculate statistics
            return {
                action: {
                    'avg': sum(times) / len(times) if times else None,
                    'min': min(times) if times else None,
                    'max': max(times) if times else None,
                    'count': len(times)
                }
                for action, times in response_times.items()
            }
        except Exception as e:
            logger.error(f"Error calculating response times: {e}")
            return {}

    def _analyze_changes(self, history: List[dict]) -> dict:
        """Analyze patterns in how suggestions are modified"""
        try:
            modifications = {
                'types': {},  # Types of modifications made
                'frequency': {},  # How often certain modifications occur
                'common_sequences': []  # Common sequences of modifications
            }
            
            for event in history:
                if event['action'] == 'modified':
                    mod_type = event.get('metadata', {}).get('modification_type', 'unknown')
                    modifications['types'].setdefault(mod_type, 0)
                    modifications['types'][mod_type] += 1
            
            # Analyze sequences
            sequences = self._extract_modification_sequences(history)
            modifications['common_sequences'] = self._find_common_sequences(sequences)
            
            return modifications
        except Exception as e:
            logger.error(f"Error analyzing changes: {e}")
            return {}

    def _track_task_assignments(self, history: List[dict]) -> dict:
        """Track how tasks are delegated and reassigned"""
        try:
            assignments = {
                'delegation_patterns': {},
                'reassignment_frequency': {},
                'completion_rates': {}
            }
            
            for event in history:
                if 'assignment' in event.get('metadata', {}):
                    assignment = event['metadata']['assignment']
                    assignments['delegation_patterns'].setdefault(assignment['from_user'], {})
                    assignments['delegation_patterns'][assignment['from_user']].setdefault(assignment['to_user'], 0)
                    assignments['delegation_patterns'][assignment['from_user']][assignment['to_user']] += 1
            
            return assignments
        except Exception as e:
            logger.error(f"Error tracking assignments: {e}")
            return {}

    def _analyze_task_breakdown(self, history: List[dict]) -> dict:
        """Analyze patterns in how tasks are broken down into subtasks"""
        try:
            breakdown_patterns = {
                'avg_subtasks': 0,
                'common_structures': {},
                'depth_distribution': {}
            }
            
            subtask_counts = []
            for event in history:
                if 'subtasks' in event.get('metadata', {}):
                    subtasks = event['metadata']['subtasks']
                    subtask_counts.append(len(subtasks))
                    
                    # Analyze structure
                    structure = self._analyze_subtask_structure(subtasks)
                    structure_key = str(structure)
                    breakdown_patterns['common_structures'].setdefault(structure_key, 0)
                    breakdown_patterns['common_structures'][structure_key] += 1
            
            if subtask_counts:
                breakdown_patterns['avg_subtasks'] = sum(subtask_counts) / len(subtask_counts)
            
            return breakdown_patterns
        except Exception as e:
            logger.error(f"Error analyzing task breakdown: {e}")
            return {}

    @staticmethod
    def _extract_modification_sequences(history: List[dict]) -> List[List[str]]:
        """Extract sequences of modifications from history"""
        sequences = []
        current_sequence = []
        
        for event in history:
            if event['action'] == 'modified':
                current_sequence.append(event.get('metadata', {}).get('modification_type', 'unknown'))
            else:
                if current_sequence:
                    sequences.append(current_sequence)
                    current_sequence = []
        
        if current_sequence:
            sequences.append(current_sequence)
        
        return sequences

    @staticmethod
    def _find_common_sequences(sequences: List[List[str]], min_length: int = 2) -> List[dict]:
        """Find common sequences of modifications"""
        sequence_counts = {}
        
        for sequence in sequences:
            if len(sequence) >= min_length:
                seq_key = tuple(sequence)
                sequence_counts.setdefault(seq_key, 0)
                sequence_counts[seq_key] += 1
        
        # Sort by frequency and convert to list of dicts
        return [
            {'sequence': list(seq), 'count': count}
            for seq, count in sorted(sequence_counts.items(), key=lambda x: x[1], reverse=True)
        ]

    @staticmethod
    def _analyze_subtask_structure(subtasks: List[dict], max_depth: int = 5) -> dict:
        """Analyze the structure of subtasks"""
        def analyze_level(tasks, current_depth=0):
            if current_depth >= max_depth or not tasks:
                return {'count': 0}
            
            result = {'count': len(tasks)}
            child_structures = [
                analyze_level(task.get('subtasks', []), current_depth + 1)
                for task in tasks
            ]
            
            if child_structures:
                result['children'] = child_structures
            
            return result
        
        return analyze_level(subtasks)


# ---------------------------------------------------------------------------
# CLASS: TemporalAnalysis
# ---------------------------------------------------------------------------
class AnalysisBase:
    """Base class for common analysis patterns"""
    
    def _calculate_completion_rate(self, tasks: List[dict]) -> float:
        """Calculate task completion rate"""
        try:
            if not tasks:
                return 0.0
            completed = len([t for t in tasks if t.get('completed_at')])
            return completed / len(tasks)
        except Exception as e:
            logger.error(f"Error calculating completion rate: {e}")
            return 0.0
            
    def _calculate_avg_completion_time(self, tasks: List[dict]) -> float:
        """Calculate average completion time in hours"""
        try:
            completion_times = []
            for task in tasks:
                if task.get('completed_at') and task.get('created_at'):
                    time_taken = (task['completed_at'] - task['created_at']) / 3600
                    completion_times.append(time_taken)
            return sum(completion_times) / len(completion_times) if completion_times else 0.0
        except Exception as e:
            logger.error(f"Error calculating average completion time: {e}")
            return 0.0

class TemporalAnalysis(AnalysisBase):
    """Analyzes time-based patterns in task completion and project phases"""
    
    def __init__(self):
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        self.analysis_file = os.path.join(self.data_dir, 'temporal_analysis.json')
        os.makedirs(self.data_dir, exist_ok=True)
        
    def analyze_patterns(self, tasks: List[dict]) -> dict:
        """Analyze temporal patterns in task completion"""
        try:
            # Get basic patterns
            patterns = self._analyze_basic_patterns(tasks)
            
            # Add additional analysis
            patterns.update({
                'optimal_times': self._analyze_optimal_times(tasks),
                'day_patterns': self._analyze_day_patterns(tasks),
                'recent_trends': self._analyze_trends(tasks)
            })
            
            # Save analysis results
            self._save_analysis(patterns)
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error analyzing temporal patterns: {e}")
            return {
                'completion_by_hour': {},
                'completion_by_day': {},
                'avg_completion_time': 0.0,
                'optimal_times': {},
                'day_patterns': {},
                'recent_trends': []
            }

    def _analyze_optimal_times(self, tasks: List[dict]) -> dict:
        """Analyze optimal times for task completion"""
        try:
            optimal_times = {
                'hourly_completion': {},
                'peak_hours': [],
                'efficiency_scores': {}
            }
            
            # Analyze hourly completion patterns
            for task in tasks:
                if task.get('completed_at'):
                    hour = datetime.fromtimestamp(task['completed_at']).hour
                    optimal_times['hourly_completion'].setdefault(hour, {
                        'count': 0,
                        'total_duration': 0,
                        'success_rate': 0
                    })
                    
                    optimal_times['hourly_completion'][hour]['count'] += 1
                    
                    if task.get('created_at'):
                        duration = task['completed_at'] - task['created_at']
                        optimal_times['hourly_completion'][hour]['total_duration'] += duration
            
            # Calculate efficiency scores and identify peak hours
            total_completions = sum(h['count'] for h in optimal_times['hourly_completion'].values())
            if total_completions > 0:
                for hour, stats in optimal_times['hourly_completion'].items():
                    avg_duration = stats['total_duration'] / stats['count'] if stats['count'] > 0 else 0
                    completion_ratio = stats['count'] / total_completions
                    
                    efficiency_score = completion_ratio * (1 / (avg_duration + 1))  # Avoid div by zero
                    optimal_times['efficiency_scores'][hour] = efficiency_score
                    
                    if efficiency_score > 0.15:  # Threshold for peak hours
                        optimal_times['peak_hours'].append(hour)
            
            return optimal_times
            
        except Exception as e:
            logger.error(f"Error analyzing optimal times: {e}")
            return {'hourly_completion': {}, 'peak_hours': [], 'efficiency_scores': {}}

    def _analyze_day_patterns(self, tasks: List[dict]) -> dict:
        """Analyze patterns in day-of-week completion"""
        try:
            day_patterns = {
                'daily_stats': {},
                'optimal_days': [],
                'workload_distribution': {}
            }
            
            # Analyze daily completion patterns
            for task in tasks:
                if task.get('completed_at'):
                    day = datetime.fromtimestamp(task['completed_at']).strftime('%A')
                    day_patterns['daily_stats'].setdefault(day, {
                        'completed': 0,
                        'total_duration': 0,
                        'avg_completion_time': 0
                    })
                    
                    day_patterns['daily_stats'][day]['completed'] += 1
                    
                    if task.get('created_at'):
                        duration = (task['completed_at'] - task['created_at']) / 3600  # hours
                        day_patterns['daily_stats'][day]['total_duration'] += duration
            
            # Calculate averages and identify optimal days
            for day, stats in day_patterns['daily_stats'].items():
                if stats['completed'] > 0:
                    stats['avg_completion_time'] = stats['total_duration'] / stats['completed']
                    
                    # Consider a day optimal if average completion time is below overall average
                    if stats['avg_completion_time'] < self._calculate_avg_completion_time(tasks):
                        day_patterns['optimal_days'].append(day)
                
                # Calculate workload distribution
                total_completed = sum(d['completed'] for d in day_patterns['daily_stats'].values())
                if total_completed > 0:
                    day_patterns['workload_distribution'][day] = stats['completed'] / total_completed
            
            return day_patterns
            
        except Exception as e:
            logger.error(f"Error analyzing day patterns: {e}")
            return {'daily_stats': {}, 'optimal_days': [], 'workload_distribution': {}}

    def _analyze_trends(self, tasks: List[dict]) -> List[dict]:
        """Analyze recent trends in task completion"""
        try:
            now = datetime.now()
            recent_tasks = [t for t in tasks if t.get('completed_at') and 
                          now - datetime.fromtimestamp(t['completed_at']) < timedelta(days=30)]
            
            trend_data = []
            if recent_tasks:
                # Group by week
                weekly_data = {}
                for task in recent_tasks:
                    completion_date = datetime.fromtimestamp(task['completed_at'])
                    week_num = completion_date.isocalendar()[1]
                    
                    weekly_data.setdefault(week_num, []).append(task)
                
                # Calculate weekly metrics
                for week, week_tasks in weekly_data.items():
                    trend_data.append({
                        "week": week,
                        "completion_count": len(week_tasks),
                        "avg_completion_time": self._calculate_avg_completion_time(week_tasks),
                        "success_rate": self._calculate_completion_rate(week_tasks),
                        "velocity": len(week_tasks) / 7  # tasks per day
                    })
            
            return sorted(trend_data, key=lambda x: x["week"])
            
        except Exception as e:
            logger.error(f"Error analyzing trends: {e}")
            return []

    def _save_analysis(self, patterns: dict):
        """Save temporal analysis results"""
        try:
            with open(self.analysis_file, 'w') as f:
                json.dump(patterns, f, indent=2)
                
        except Exception as e:
            logger.error(f"Error saving temporal analysis: {e}")

    def _find_optimal_suggestion_times(self, task_history: List[dict]) -> dict:
        """Find optimal times for task suggestions based on success rates"""
        try:
            hourly_success = {i: {"attempts": 0, "successes": 0} for i in range(24)}
            
            for task in task_history:
                if task.get('created_at'):
                    hour = datetime.fromtimestamp(task['created_at']).hour
                    hourly_success[hour]["attempts"] += 1
                    
                    if task.get('completed_at'):
                        hourly_success[hour]["successes"] += 1
            
            # Calculate success rates and identify optimal times
            optimal_hours = []
            for hour, stats in hourly_success.items():
                if stats["attempts"] > 0:
                    success_rate = stats["successes"] / stats["attempts"]
                    if success_rate > 0.7:  # Consider hours with >70% success rate optimal
                        optimal_hours.append({
                            "hour": hour,
                            "success_rate": success_rate,
                            "sample_size": stats["attempts"]
                        })
            
            return {
                "optimal_hours": sorted(optimal_hours, key=lambda x: x["success_rate"], reverse=True),
                "hourly_stats": hourly_success
            }
        except Exception as e:
            logger.error(f"Error finding optimal times: {e}")
            return {}

    def _analyze_day_patterns(self, task_history: List[dict]) -> dict:
        """Analyze patterns in day-to-day task completion"""
        try:
            daily_patterns = {
                "weekday_distribution": {},
                "day_transitions": {},
                "weekly_cycles": []
            }
            
            # Analyze weekday distribution
            for task in task_history:
                if task.get('completed_at'):
                    weekday = datetime.fromtimestamp(task['completed_at']).strftime('%A')
                    daily_patterns["weekday_distribution"].setdefault(weekday, 0)
                    daily_patterns["weekday_distribution"][weekday] += 1
            
            # Analyze day-to-day transitions
            sorted_tasks = sorted(task_history, key=lambda x: x.get('completed_at', 0))
            for i in range(len(sorted_tasks) - 1):
                if sorted_tasks[i].get('completed_at') and sorted_tasks[i+1].get('completed_at'):
                    day1 = datetime.fromtimestamp(sorted_tasks[i]['completed_at']).strftime('%A')
                    day2 = datetime.fromtimestamp(sorted_tasks[i+1]['completed_at']).strftime('%A')
                    transition = f"{day1}->{day2}"
                    daily_patterns["day_transitions"].setdefault(transition, 0)
                    daily_patterns["day_transitions"][transition] += 1
            
            # Analyze weekly cycles
            weekly_tasks = self._group_by_week(task_history)
            daily_patterns["weekly_cycles"] = self._analyze_weekly_patterns(weekly_tasks)
            
            return daily_patterns
        except Exception as e:
            logger.error(f"Error analyzing day patterns: {e}")
            return {}

    def _analyze_project_phase_correlation(self, task_history: List[dict]) -> dict:
        """Analyze how project phases affect task completion patterns"""
        try:
            phase_patterns = {
                "phase_success_rates": {},
                "phase_velocity": {},
                "transition_impacts": {},
                "phase_duration_stats": {}
            }
            
            # Group tasks by project phase
            phase_groups = {}
            for task in task_history:
                phase = task.get('project_phase', 'unknown')
                phase_groups.setdefault(phase, []).append(task)
            
            # Calculate success rates per phase
            for phase, tasks in phase_groups.items():
                completed = len([t for t in tasks if t.get('completed_at')])
                total = len(tasks)
                phase_patterns["phase_success_rates"][phase] = {
                    "success_rate": completed / total if total > 0 else 0,
                    "sample_size": total
                }
            
            # Calculate velocity per phase
            for phase, tasks in phase_groups.items():
                if tasks:
                    start_time = min(t.get('created_at', float('inf')) for t in tasks)
                    end_time = max(t.get('completed_at', 0) for t in tasks)
                    duration = end_time - start_time if end_time > start_time else 1
                    completed_tasks = len([t for t in tasks if t.get('completed_at')])
                    phase_patterns["phase_velocity"][phase] = completed_tasks / (duration / 86400)  # tasks per day
            
            # Analyze phase transitions
            phase_patterns["transition_impacts"] = self._analyze_phase_transitions(task_history)
            
            # Calculate phase duration statistics
            phase_patterns["phase_duration_stats"] = self._calculate_phase_durations(phase_groups)
            
            return phase_patterns
        except Exception as e:
            logger.error(f"Error analyzing project phase correlation: {e}")
            return {}

    @staticmethod
    def _group_by_week(task_history: List[dict]) -> Dict[int, List[dict]]:
        """Group tasks by week number"""
        weekly_tasks = {}
        for task in task_history:
            if task.get('completed_at'):
                week = datetime.fromtimestamp(task['completed_at']).isocalendar()[1]
                weekly_tasks.setdefault(week, []).append(task)
        return weekly_tasks

    @staticmethod
    def _analyze_weekly_patterns(weekly_tasks: Dict[int, List[dict]]) -> List[dict]:
        """Analyze patterns in weekly task completion"""
        weekly_patterns = []
        for week, tasks in weekly_tasks.items():
            pattern = {
                "week": week,
                "total_tasks": len(tasks),
                "daily_distribution": {},
                "task_types": {}
            }
            
            for task in tasks:
                # Daily distribution
                if task.get('completed_at'):
                    day = datetime.fromtimestamp(task['completed_at']).strftime('%A')
                    pattern["daily_distribution"].setdefault(day, 0)
                    pattern["daily_distribution"][day] += 1
                
                # Task type distribution
                task_type = task.get('type', 'unknown')
                pattern["task_types"].setdefault(task_type, 0)
                pattern["task_types"][task_type] += 1
            
            weekly_patterns.append(pattern)
        
        return weekly_patterns

    @staticmethod
    def _analyze_phase_transitions(task_history: List[dict]) -> dict:
        """Analyze the impact of phase transitions on task completion"""
        transitions = {}
        sorted_tasks = sorted(task_history, key=lambda x: x.get('created_at', 0))
        
        for i in range(len(sorted_tasks) - 1):
            current_phase = sorted_tasks[i].get('project_phase')
            next_phase = sorted_tasks[i + 1].get('project_phase')
            
            if current_phase and next_phase and current_phase != next_phase:
                transition = f"{current_phase}->{next_phase}"
                transitions.setdefault(transition, {
                    "count": 0,
                    "avg_completion_time": 0,
                    "success_rate": 0
                })
                transitions[transition]["count"] += 1
                
                # Calculate completion time if both tasks were completed
                if (sorted_tasks[i].get('completed_at') and 
                    sorted_tasks[i + 1].get('completed_at')):
                    completion_time = (sorted_tasks[i + 1]['completed_at'] - 
                                    sorted_tasks[i + 1]['created_at'])
                    transitions[transition]["avg_completion_time"] += completion_time
        
        # Calculate averages
        for stats in transitions.values():
            if stats["count"] > 0:
                stats["avg_completion_time"] /= stats["count"]
        
        return transitions

    @staticmethod
    def _calculate_phase_durations(phase_groups: Dict[str, List[dict]]) -> dict:
        """Calculate duration statistics for each project phase"""
        duration_stats = {}
        
        for phase, tasks in phase_groups.items():
            completed_tasks = [t for t in tasks if t.get('completed_at') and t.get('created_at')]
            if completed_tasks:
                durations = [(t['completed_at'] - t['created_at']) / 3600 for t in completed_tasks]  # hours
                duration_stats[phase] = {
                    "avg_duration": sum(durations) / len(durations),
                    "min_duration": min(durations),
                    "max_duration": max(durations),
                    "sample_size": len(completed_tasks)
                }
        
        return duration_stats

    def _analyze_basic_patterns(self, tasks: List[dict]) -> dict:
        """Analyze basic temporal patterns in task completion"""
        try:
            patterns = {
                'completion_by_hour': {},
                'completion_by_day': {},
                'avg_completion_time': 0.0
            }
            
            completion_times = []
            for task in tasks:
                created = task.get('created_at')
                completed = task.get('completed_at')
                
                if created and completed:
                    # Convert timestamps to datetime
                    if isinstance(created, (int, float)):
                        created = datetime.fromtimestamp(created)
                    if isinstance(completed, (int, float)):
                        completed = datetime.fromtimestamp(completed)
                    
                    # Record completion hour and day
                    hour = completed.hour
                    day = completed.strftime('%A')
                    
                    patterns['completion_by_hour'][hour] = patterns['completion_by_hour'].get(hour, 0) + 1
                    patterns['completion_by_day'][day] = patterns['completion_by_day'].get(day, 0) + 1
                    
                    # Calculate completion time in hours
                    completion_time = (completed - created).total_seconds() / 3600
                    completion_times.append(completion_time)
            
            # Calculate average completion time
            if completion_times:
                patterns['avg_completion_time'] = sum(completion_times) / len(completion_times)
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error analyzing basic patterns: {e}")
            return {
                'completion_by_hour': {},
                'completion_by_day': {},
                'avg_completion_time': 0.0
            }


# ---------------------------------------------------------------------------
# CLASS: FeedbackCollector
# ---------------------------------------------------------------------------
class FeedbackCollector:
    """Collects and analyzes feedback from multiple sources"""

    def __init__(self, github_monitor: GitHubActivityMonitor, user_tracker: UserInteractionTracker, temporal_analyzer: TemporalAnalysis):
        self.github_monitor = github_monitor
        self.user_tracker = user_tracker
        self.temporal_analyzer = temporal_analyzer
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        self.tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
        
        # Update file paths
        self.action_outcomes_file = os.path.join(self.data_dir, 'action_outcomes.jsonl')
        self.success_patterns_file = os.path.join(self.data_dir, 'success_patterns.json')
        self.temporal_analysis_file = os.path.join(self.data_dir, 'temporal_analysis.json')
        self.completed_tasks_file = os.path.join(self.tasks_dir, 'completed_tasks.json')

    def calculate_feedback_score(self, interactions: List[dict], completion_time: float) -> float:
        """Calculate a feedback score based on interactions and completion time"""
        try:
            base_score = 1.0
            
            # Adjust score based on completion time
            if completion_time:
                # Normalize completion time (e.g., if completed within 24 hours, good score)
                time_factor = min(1.0, 24.0 / max(1.0, completion_time))
                base_score *= (0.5 + 0.5 * time_factor)
            
            # Ensure interactions is a list of dicts
            if isinstance(interactions, list):
                for interaction in interactions:
                    if isinstance(interaction, dict):
                        action = interaction.get('action')
                        if action == 'accepted':
                            base_score *= 1.2
                        elif action == 'rejected':
                            base_score *= 0.8
                        elif action == 'modified':
                            base_score *= 0.9
            
            return min(1.0, max(0.0, base_score))
            
        except Exception as e:
            logger.error(f"Error calculating feedback score: {e}")
            return 0.5

    def collect_feedback(self, suggestion_id: str) -> dict:
        """Collect comprehensive feedback with scoring"""
        try:
            # Get user interactions
            interactions = self.user_tracker.track_interactions(suggestion_id)
            
            # Get task history including completed tasks
            task_history = self.github_monitor.get_tasks().get("tasks", [])
            
            # Calculate completion time if task was completed
            completion_time = self._calculate_completion_time(suggestion_id, task_history)
            
            # Calculate feedback score
            feedback_score = self.calculate_feedback_score(interactions, completion_time)
            
            # Store completion information
            self._store_completion_data(task_history)
            
            feedback_data = {
                "suggestion_id": suggestion_id,
                "timestamp": time.time(),
                "feedback_score": feedback_score,
                "completion_time": completion_time,
                "interactions": interactions,
                "metrics": {
                    "completion_count": len([t for t in task_history if t.get('completed_at')]),
                    "total_tasks": len(task_history)
                }
            }
            
            return feedback_data
            
        except Exception as e:
            logger.error(f"Error collecting feedback: {e}")
            return {}

    def _store_completion_data(self, task_history: List[dict]):
        """Store completion data in a structured format"""
        try:
            # Ensure the data directory exists
            os.makedirs(self.data_dir, exist_ok=True)
            
            # Get completed tasks
            completed_tasks = []
            if os.path.exists(self.completed_tasks_file):
                with open(self.completed_tasks_file, 'r') as f:
                    completed_tasks = json.load(f)
            
            # Calculate metrics
            total_tasks = len(task_history)
            completed_count = len(completed_tasks)
            completion_rate = completed_count / total_tasks if total_tasks > 0 else 0
            
            # Update success patterns
            success_patterns = {
                'completion_rate': completion_rate,
                'completed_count': completed_count,
                'last_updated': int(time.time()),
                'common_blockers': self._analyze_blockers(completed_tasks),
                'successful_labels': self._analyze_successful_labels(completed_tasks)
            }
            
            with open(self.success_patterns_file, 'w') as f:
                json.dump(success_patterns, f, indent=2)
                
            logger.info(f"Updated feedback data with {completed_count} completed tasks")
            
        except Exception as e:
            logger.error(f"Error storing completion data: {e}")

    def _load_feedback_data(self) -> dict:
        """Load feedback data from all sources"""
        try:
            feedback_data = {
                'success_patterns': self._load_success_patterns(),
                'temporal_analysis': self._load_temporal_analysis(),  # Use the new method
                'action_outcomes': []
            }
            
            # Load action outcomes
            if os.path.exists(self.action_outcomes_file):
                try:
                    with jsonlines.open(self.action_outcomes_file) as reader:
                        feedback_data['action_outcomes'] = list(reader)
                except Exception as e:
                    logger.warning(f"Error reading action_outcomes.jsonl: {e}")
            
            return feedback_data
            
        except Exception as e:
            logger.error(f"Error loading feedback data: {e}")
            return {
                'success_patterns': self._load_success_patterns(),
                'temporal_analysis': self._load_temporal_analysis(),
                'action_outcomes': []
            }

    def calculate_outcome_metrics(self, task_history: List[dict], interactions: List[dict]) -> dict:
        """Calculate comprehensive outcome metrics"""
        try:
            metrics = {
                "task_metrics": self._calculate_task_metrics(task_history),
                "interaction_metrics": self._calculate_interaction_metrics(interactions),
                "correlation_metrics": self._calculate_correlation_metrics(task_history, interactions),
                "trend_metrics": self._calculate_trend_metrics(task_history, interactions)
            }
            return metrics
        except Exception as e:
            logger.error(f"Error calculating outcome metrics: {e}")
            return {}

    def identify_success_patterns(self, task_history: List[dict], interactions: List[dict]) -> dict:
        """Identify patterns that lead to successful outcomes"""
        try:
            patterns = {
                "task_patterns": self._analyze_successful_tasks(task_history),
                "interaction_patterns": self._analyze_successful_interactions(interactions),
                "timing_patterns": self._analyze_timing_success(task_history, interactions),
                "sequence_patterns": self._analyze_successful_sequences(task_history, interactions)
            }
            return patterns
        except Exception as e:
            logger.error(f"Error identifying success patterns: {e}")
            return {}

    def identify_failure_patterns(self, task_history: List[dict], interactions: List[dict]) -> dict:
        """Identify patterns that lead to unsuccessful outcomes"""
        try:
            patterns = {
                "task_failures": self._analyze_failed_tasks(task_history),
                "interaction_failures": self._analyze_failed_interactions(interactions),
                "bottlenecks": self._identify_bottlenecks(task_history, interactions),
                "risk_factors": self._identify_risk_factors(task_history, interactions)
            }
            return patterns
        except Exception as e:
            logger.error(f"Error identifying failure patterns: {e}")
            return {}

    def generate_recommendations(self, task_history: List[dict], interactions: List[dict]) -> List[dict]:
        """Generate actionable recommendations based on feedback analysis"""
        try:
            recommendations = []
            
            # Analyze task optimization opportunities
            task_recommendations = self._generate_task_recommendations(task_history)
            recommendations.extend(task_recommendations)
            
            # Analyze interaction improvements
            interaction_recommendations = self._generate_interaction_recommendations(interactions)
            recommendations.extend(interaction_recommendations)
            
            # Analyze process improvements
            process_recommendations = self._generate_process_recommendations(task_history, interactions)
            recommendations.extend(process_recommendations)
            
            return recommendations
        except Exception as e:
            logger.error(f"Error generating recommendations: {e}")
            return []

    def _calculate_task_metrics(self, task_history: List[dict]) -> dict:
        """Calculate task-specific metrics"""
        try:
            completed_tasks = [t for t in task_history if t.get('completed_at')]
            total_tasks = len(task_history)
            
            return {
                "completion_rate": len(completed_tasks) / total_tasks if total_tasks > 0 else 0,
                "avg_completion_time": self._calculate_avg_completion_time(completed_tasks),
                "priority_distribution": self._calculate_priority_distribution(task_history),
                "type_distribution": self._calculate_type_distribution(task_history)
            }
        except Exception as e:
            logger.error(f"Error calculating task metrics: {e}")
            return {}

    def _calculate_interaction_metrics(self, interactions: List[dict]) -> dict:
        """Calculate interaction-specific metrics"""
        try:
            return {
                "response_times": self._calculate_response_times(interactions),
                "modification_rates": self._calculate_modification_rates(interactions),
                "acceptance_rates": self._calculate_acceptance_rates(interactions),
                "engagement_metrics": self._calculate_engagement_metrics(interactions)
            }
        except Exception as e:
            logger.error(f"Error calculating interaction metrics: {e}")
            return {}

    def _calculate_correlation_metrics(self, task_history: List[dict], interactions: List[dict]) -> dict:
        """Calculate correlation between tasks and interactions"""
        try:
            return {
                "interaction_success_correlation": self._correlate_interactions_with_success(task_history, interactions),
                "timing_impact": self._analyze_timing_impact(task_history, interactions),
                "modification_impact": self._analyze_modification_impact(task_history, interactions)
            }
        except Exception as e:
            logger.error(f"Error calculating correlation metrics: {e}")
            return {}

    def _calculate_trend_metrics(self, task_history: List[dict], interactions: List[dict]) -> dict:
        """Calculate trend metrics over time"""
        try:
            return {
                "completion_trends": self._analyze_completion_trends(task_history),
                "interaction_trends": self._analyze_interaction_trends(interactions),
                "quality_trends": self._analyze_quality_trends(task_history, interactions)
            }
        except Exception as e:
            logger.error(f"Error calculating trend metrics: {e}")
            return {}

    # Helper methods for success pattern analysis
    def _analyze_successful_tasks(self, tasks: List[dict]) -> dict:
        """Analyze patterns in successfully completed tasks"""
        successful_patterns = {
            "common_attributes": {},
            "timing_patterns": {},
            "dependency_patterns": {}
        }
        # Implementation details...
        return successful_patterns

    def _analyze_successful_interactions(self, interactions: List[dict]) -> dict:
        """Analyze patterns in successful interactions"""
        interaction_patterns = {
            "effective_sequences": [],
            "optimal_timing": {},
            "user_preferences": {}
        }
        # Implementation details...
        return interaction_patterns

    # Helper methods for failure pattern analysis
    def _analyze_failed_tasks(self, tasks: List[dict]) -> dict:
        """Analyze patterns in failed or incomplete tasks"""
        failure_patterns = {
            "common_blockers": {},
            "risk_indicators": {},
            "abandonment_patterns": {}
        }
        # Implementation details...
        return failure_patterns

    def _identify_bottlenecks(self, tasks: List[dict], interactions: List[dict]) -> dict:
        """Identify system and process bottlenecks"""
        bottlenecks = {
            "process_bottlenecks": {},
            "resource_constraints": {},
            "communication_gaps": {}
        }
        # Implementation details...
        return bottlenecks

    # Helper methods for recommendation generation
    def _generate_task_recommendations(self, tasks: List[dict]) -> List[dict]:
        """Generate task-specific recommendations"""
        recommendations = []
        # Implementation details...
        return recommendations

    def _generate_process_recommendations(self, tasks: List[dict], interactions: List[dict]) -> List[dict]:
        """Generate process improvement recommendations"""
        recommendations = []
        # Implementation details...
        return recommendations

    def aggregate_feedback(self, *feedback_sources: dict) -> dict:
        """Aggregate and normalize feedback from multiple sources"""
        try:
            aggregated = {
                "timestamp": time.time(),
                "metrics": {},
                "patterns": {},
                "recommendations": []
            }
            
            for source in feedback_sources:
                self._merge_metrics(aggregated["metrics"], source.get("metrics", {}))
                self._merge_patterns(aggregated["patterns"], source.get("patterns", {}))
                self._merge_recommendations(aggregated["recommendations"], source.get("recommendations", []))
            
            return aggregated
        except Exception as e:
            logger.error(f"Error aggregating feedback: {e}")
            return {}

    @staticmethod
    def _merge_metrics(target: dict, source: dict) -> None:
        """Merge metrics with proper weighting and normalization"""
        for key, value in source.items():
            if key not in target:
                target[key] = value
            else:
                # Implement proper merging logic based on metric type
                if isinstance(value, (int, float)):
                    target[key] = (target[key] + value) / 2
                elif isinstance(value, dict):
                    if not isinstance(target[key], dict):
                        target[key] = {}
                    FeedbackCollector._merge_metrics(target[key], value)

    @staticmethod
    def _merge_patterns(target: dict, source: dict) -> None:
        """Merge patterns with frequency counting"""
        for key, value in source.items():
            if key not in target:
                target[key] = value
            else:
                if isinstance(value, dict):
                    if not isinstance(target[key], dict):
                        target[key] = {}
                    FeedbackCollector._merge_patterns(target[key], value)
                elif isinstance(value, list):
                    target[key].extend(value)

    @staticmethod
    def _merge_recommendations(target: List, source: List) -> None:
        """Merge recommendations with deduplication"""
        seen = {str(r): r for r in target}
        for rec in source:
            rec_key = str(rec)
            if rec_key not in seen:
                target.append(rec)

    def _calculate_completion_time(self, suggestion_id: str, task_history: List[dict]) -> float:
        """Calculate time taken to complete a task from suggestion to completion"""
        try:
            # Find the task in history
            task = next((t for t in task_history if t.get('suggestion_id') == suggestion_id), None)
            if not task:
                return 0.0
                
            # Get timestamps
            suggested_at = task.get('suggested_at')
            completed_at = task.get('completed_at')
            
            if not suggested_at or not completed_at:
                return 0.0
                
            # Convert to datetime objects
            suggested_time = datetime.fromtimestamp(suggested_at)
            completed_time = datetime.fromtimestamp(completed_at)
            
            # Calculate hours difference
            time_diff = completed_time - suggested_time
            hours_taken = time_diff.total_seconds() / 3600
            
            return hours_taken
            
        except Exception as e:
            logger.error(f"Error calculating completion time: {e}")
            return 0.0

    def update_feedback(self):
        """Periodic update of feedback data"""
        try:
            # Get latest tasks
            task_history = self.github_monitor.get_tasks().get("tasks", [])
            
            # Update completion data
            self._store_completion_data(task_history)
            
            # Update temporal patterns
            self.temporal_analyzer.analyze_patterns(task_history)
            
            # Log success
            logger.info("Updated all feedback stores")
            
        except Exception as e:
            logger.error(f"Error updating feedback: {e}")

    def _analyze_blockers(self, completed_tasks: List[dict]) -> List[str]:
        """Analyze common blockers from completed tasks"""
        try:
            blockers = {}
            for task in completed_tasks:
                task_blockers = task.get('blockers', [])
                for blocker in task_blockers:
                    blockers[blocker] = blockers.get(blocker, 0) + 1
            
            # Sort by frequency and return top blockers
            sorted_blockers = sorted(blockers.items(), key=lambda x: x[1], reverse=True)
            return [b[0] for b in sorted_blockers[:5]]  # Return top 5 blockers
            
        except Exception as e:
            logger.error(f"Error analyzing blockers: {e}")
            return []

    def _analyze_successful_labels(self, completed_tasks: List[dict]) -> List[str]:
        """Analyze labels from successfully completed tasks"""
        try:
            labels = {}
            for task in completed_tasks:
                if task.get('completed_at'):  # Only consider actually completed tasks
                    task_labels = task.get('labels', [])
                    for label in task_labels:
                        labels[label] = labels.get(label, 0) + 1
            
            # Sort by frequency and return top labels
            sorted_labels = sorted(labels.items(), key=lambda x: x[1], reverse=True)
            return [l[0] for l in sorted_labels[:5]]  # Return top 5 labels
            
        except Exception as e:
            logger.error(f"Error analyzing successful labels: {e}")
            return []


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

    def update_file(self, path: str, message: str, content: str):
        """Update a file in the repository"""
        try:
            # Ensure file exists first
            self.ensure_file_exists(path, content)
            
            # Get current content to get the SHA
            file_content = self.repo.get_contents(path)
            
            # Update the file
            self.repo.update_file(
                path=path,
                message=message,
                content=content,
                sha=file_content.sha
            )
            print(f"Successfully updated {path}")
        except Exception as e:
            print(f"Error updating file {path}: {e}")
            raise

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
    """Aggregates context from multiple sources"""
    
    def __init__(self, github_monitor: GitHubActivityMonitor):
        self.github_monitor = github_monitor
        # Use the singleton LLM service instead of creating a new one
        self.context_compressor = ContextCompressor(get_llm_service())
        
        # Initialize file paths
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        self.tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
        
        # Define file paths
        self.success_patterns_file = os.path.join(self.data_dir, 'success_patterns.json')
        self.temporal_analysis_file = os.path.join(self.data_dir, 'temporal_analysis.json')
        self.action_outcomes_file = os.path.join(self.data_dir, 'action_outcomes.jsonl')
        
        # Ensure directories exist
        os.makedirs(self.data_dir, exist_ok=True)

    def _load_success_patterns(self) -> dict:
        """Load success patterns from file"""
        try:
            if os.path.exists(self.success_patterns_file):
                with open(self.success_patterns_file, 'r') as f:
                    return json.load(f)
            return {
                'completion_rate': 0.0,
                'avg_completion_time': 0.0,
                'common_blockers': [],
                'successful_labels': []
            }
        except Exception as e:
            logger.error(f"Error loading success patterns: {e}")
            return {
                'completion_rate': 0.0,
                'avg_completion_time': 0.0,
                'common_blockers': [],
                'successful_labels': []
            }

    def _load_temporal_analysis(self) -> dict:
        """Load temporal analysis data from file"""
        try:
            if os.path.exists(self.temporal_analysis_file):
                with open(self.temporal_analysis_file, 'r') as f:
                    return json.load(f)
            return {
                'optimal_times': {},
                'day_patterns': {},
                'recent_trends': []
            }
        except Exception as e:
            logger.error(f"Error loading temporal analysis: {e}")
            return {
                'optimal_times': {},
                'day_patterns': {},
                'recent_trends': []
            }

    def _load_feedback_data(self) -> dict:
        """Load feedback data from all sources"""
        try:
            feedback_data = {
                'success_patterns': self._load_success_patterns(),
                'temporal_analysis': self._load_temporal_analysis(),  # Use the new method
                'action_outcomes': []
            }
            
            # Load action outcomes
            if os.path.exists(self.action_outcomes_file):
                try:
                    with jsonlines.open(self.action_outcomes_file) as reader:
                        feedback_data['action_outcomes'] = list(reader)
                except Exception as e:
                    logger.warning(f"Error reading action_outcomes.jsonl: {e}")
            
            return feedback_data
            
        except Exception as e:
            logger.error(f"Error loading feedback data: {e}")
            return {
                'success_patterns': self._load_success_patterns(),
                'temporal_analysis': self._load_temporal_analysis(),
                'action_outcomes': []
            }

    def aggregate_context(self, query: str, repo_contents: dict) -> dict:
        """Aggregate context from various sources for the LLM"""
        try:
            # Get current tasks
            tasks = repo_contents.get("tasks", [])
            
            # Process any large context documents
            processed_contents = {}
            for path, content in repo_contents.get("raw_contents", {}).items():
                try:
                    # Save content to file if it's not already on disk
                    file_path = os.path.join('tasks', os.path.basename(path))
                    if not os.path.exists(file_path):
                        with open(file_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                    
                    # Get compressed version if needed
                    compressed_path = self.context_compressor.get_or_create_compressed_version(file_path)
                    
                    # Read the appropriate version
                    with open(compressed_path, 'r', encoding='utf-8') as f:
                        processed_contents[path] = f.read()
                        
                except Exception as e:
                    logger.error(f"Error processing content for {path}: {e}")
                    processed_contents[path] = content  # Use original if compression fails
            
            # Build context dictionary
            context = {
                "user_query": query,
                "repo_info": {
                    "tasks": tasks,
                    "raw_contents": processed_contents,
                    "feedback": {
                        "action_outcomes": self._load_feedback_data(),
                        "success_patterns": self._load_success_patterns(),
                        "temporal_analysis": self._load_temporal_analysis()
                    }
                }
            }
            
            return context
            
        except Exception as e:
            logger.error(f"Error aggregating context: {e}")
            raise

# ---------------------------------------------------------------------------
# CLASS: LLMService (Gemini integration)
# ---------------------------------------------------------------------------
class LLMService:
    """Service for interacting with LLM APIs"""
    def __init__(self):
        # Change default provider to anthropic
        self.provider = 'anthropic'
        
        # Load API keys from environment
        self.anthropic_key = os.getenv('ANTHROPIC_API_KEY')
        self.gemini_key = os.getenv('GOOGLE_API_KEY')
        self.openai_key = os.getenv('OPENAI_API_KEY')
        
        # Verify default provider's API key is available
        if not self.anthropic_key:
            logger.warning("ANTHROPIC_API_KEY not set, falling back to Gemini")
            self.provider = 'gemini'
            if not self.gemini_key:
                raise ValueError("Neither ANTHROPIC_API_KEY nor GOOGLE_API_KEY is set")
        
        # Set up endpoints
        self.endpoints = {
            'anthropic': "https://api.anthropic.com/v1/messages",
            'gemini': "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            'openai': "https://api.openai.com/v1/chat/completions"
        }
        
        self.rate_limiter = GeminiRateLimiter()  # We'll keep this for Gemini fallback
        self.rate_limit_handler = RateLimitHandler()
    
    def set_provider(self, provider: str):
        """Change the LLM provider"""
        if provider not in self.endpoints:
            raise ValueError(f"Unsupported provider: {provider}")
            
        # Check if API key is configured for the requested provider
        if provider == 'anthropic' and not self.anthropic_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        elif provider == 'openai' and not self.openai_key:
            raise ValueError("OPENAI_API_KEY not configured")
            
        self.provider = provider
        logger.info(f"Switched LLM provider to: {provider}")
    
    def generate_suggestion(self, context: dict) -> str:
        """Generate suggestion using the selected LLM provider with rate limit handling"""
        try:
            return self.rate_limit_handler.execute_with_retry(
                self._generate_with_provider,
                context
            )
        except Exception as e:
            logger.error(f"Error generating suggestion: {e}")
            raise
            
    def _generate_with_provider(self, context: dict) -> str:
        """Internal method to generate based on provider"""
        if self.provider == 'gemini':
            return self._generate_gemini(context)
        elif self.provider == 'anthropic':
            return self._generate_anthropic(context)
        elif self.provider == 'openai':
            return self._generate_openai(context)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
    
    def _generate_gemini(self, context: dict) -> str:
        """Generate suggestion using Gemini"""
        try:
            # Construct the endpoint URL with API key
            endpoint = f"{self.endpoints['gemini']}?key={self.gemini_key}"
            
            payload = {
                "contents": [{
                    "parts": [{"text": self._construct_prompt(context)}]
                }]
            }
            
            headers = {'Content-Type': 'application/json'}
            response = requests.post(endpoint, json=payload, headers=headers)
            
            if response.status_code != 200:
                raise Exception(f"Gemini API error: {response.text}")
            
            return self._parse_gemini_response(response.json())
            
        except Exception as e:
            logger.error(f"Error in Gemini API call: {e}")
            raise
    
    def _generate_anthropic(self, context: dict) -> str:
        """Generate suggestion using Anthropic's Claude"""
        try:
            headers = {
                'Content-Type': 'application/json',
                'x-api-key': self.anthropic_key,
                'anthropic-version': '2023-06-01'
            }
            
            prompt = self._construct_prompt(context)
            
            payload = {
                "model": "claude-3-sonnet-20240229",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096
            }
            
            response = requests.post(
                self.endpoints['anthropic'],
                headers=headers,
                json=payload
            )
            
            if response.status_code != 200:
                raise Exception(f"Anthropic API error: {response.text}")
                
            response_data = response.json()
            return response_data['content'][0]['text']
            
        except Exception as e:
            logger.error(f"Error in Anthropic API call: {e}")
            raise
    
    def _generate_openai(self, context: dict) -> str:
        """Generate suggestion using OpenAI"""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.openai_key}'
        }
        
        payload = {
            "model": "gpt-4o",  # Changed from gpt-4-turbo-preview to gpt-4
            "messages": [{
                "role": "user",
                "content": self._construct_prompt(context)
            }],
            "max_tokens": 1024
        }
        
        response = requests.post(self.endpoints['openai'], json=payload, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"OpenAI API error: {response.text}")
        
        return self._parse_openai_response(response.json())
    
    def _parse_gemini_response(self, response_data: dict) -> str:
        """Parse Gemini API response"""
        if 'candidates' in response_data:
            for candidate in response_data['candidates']:
                if 'content' in candidate:
                    content = candidate['content']
                    if 'parts' in content:
                        for part in content['parts']:
                            if 'text' in part:
                                return part['text']
        raise ValueError("Unexpected Gemini response structure")
    
    def _parse_anthropic_response(self, response_data: dict) -> str:
        """Parse Anthropic API response"""
        if 'content' in response_data:
            return response_data['content'][0]['text']
        raise ValueError("Unexpected Anthropic response structure")
    
    def _parse_openai_response(self, response_data: dict) -> str:
        """Parse OpenAI API response"""
        if 'choices' in response_data:
            return response_data['choices'][0]['message']['content']
        raise ValueError("Unexpected OpenAI response structure")

    def _construct_prompt(self, context: dict) -> str:
        """Construct the prompt for LLM from context"""
        try:
            # Get system prompt
            system_prompt = ""
            system_prompt_path = os.path.join('tasks', 'system_prompt.txt')
            if os.path.exists(system_prompt_path):
                with open(system_prompt_path, 'r', encoding='utf-8') as f:
                    system_prompt = f.read().strip()
            else:
                logger.error("System prompt file not found")
                raise FileNotFoundError("system_prompt.txt not found")

            # Extract active tasks (not completed)
            active_tasks = [t for t in context.get('repo_info', {}).get('tasks', []) 
                           if not t.get('completed_at')]
            
            # Sort tasks by due date and priority
            def sort_key(task):
                due_date = task.get('due') or '9999-12-31'
                priority = task.get('priority', 999)
                return (due_date, priority)
                
            active_tasks.sort(key=sort_key)
            
            # Limit number of tasks to prevent token overflow
            active_tasks = active_tasks[:10]  # Only include 10 most relevant tasks

            # Format the prompt
            prompt = f"""{system_prompt}

Current Active Tasks:
{json.dumps(active_tasks, indent=2)}

User Query:
{context.get('user_query', '')}

Please provide a response following the system prompt format exactly.
"""
            return prompt

        except Exception as e:
            logger.error(f"Error constructing prompt: {e}")
            raise

class ContextCompressor:
    """Compresses large context documents using LLM summarization"""
    
    def __init__(self, llm_service: LLMService):
        self.llm_service = llm_service
        self.cache_dir = os.path.join('tasks', 'reduced_context')
        self.compression_index_path = os.path.join(self.cache_dir, 'compression_index.json')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_manager = CacheManager(self.cache_dir)
        
    def compress_context(self, context: dict, max_tokens: int = 4000) -> dict:
        """Compress context to fit within token limit"""
        try:
            cache_key = f"context_{hash(str(context))}"
            cached = self.cache_manager.get(cache_key)
            if cached:
                return cached
                
            compressed = self._do_compression(context, max_tokens)
            self.cache_manager.set(cache_key, compressed)
            return compressed
            
        except Exception as e:
            error = ErrorHandler.handle_data_error(e, "context_compression")
            logger.error(f"Compression error: {error}")
            return context  # Return original on error
            
    def _do_compression(self, context: dict, max_tokens: int) -> dict:
        """Perform actual context compression"""
        try:
            compressed_context = context.copy()
            
            # Compress large text fields in repo contents
            if 'repo_info' in compressed_context and 'raw_contents' in compressed_context['repo_info']:
                compressed_contents = {}
                for path, content in compressed_context['repo_info']['raw_contents'].items():
                    if len(content) > max_tokens * 4:  # Rough character estimate
                        prompt = f"""Please compress this content while preserving key information:
                        1. Maintain all technical details and specific data
                        2. Keep task descriptions and requirements
                        3. Preserve dates, numbers, and proper nouns
                        4. Remove redundant explanations
                        5. Target ~50% length reduction

                        Content:
                        {content[:max_tokens * 4]}  # Limit initial input
                        """
                        
                        try:
                            compressed = self.llm_service.generate_suggestion({"user_query": prompt})
                            compressed_contents[path] = compressed
                        except Exception as e:
                            logger.error(f"Error compressing {path}: {e}")
                            compressed_contents[path] = content[:max_tokens * 4]  # Truncate as fallback
                    else:
                        compressed_contents[path] = content
                        
                compressed_context['repo_info']['raw_contents'] = compressed_contents
                
            # Compress task history if needed
            if 'repo_info' in compressed_context and 'tasks' in compressed_context['repo_info']:
                tasks = compressed_context['repo_info']['tasks']
                if len(tasks) > 50:  # Arbitrary limit
                    # Keep most recent and highest priority tasks
                    sorted_tasks = sorted(
                        tasks,
                        key=lambda x: (
                            x.get('completed_at', float('inf')),  # Uncompleted first
                            x.get('priority', 999),  # Higher priority first
                            x.get('created_at', 0)  # More recent first
                        )
                    )
                    compressed_context['repo_info']['tasks'] = sorted_tasks[:50]
            
            return compressed_context
            
        except Exception as e:
            logger.error(f"Error in context compression: {e}")
            return context  # Return original if compression fails

class ErrorHandler:
    """Centralized error handling"""
    
    @staticmethod
    def handle_api_error(e: Exception, context: str) -> dict:
        """Handle API and network related errors"""
        if isinstance(e, RateLimitExceededException):
            logger.warning(f"Rate limit exceeded in {context}")
            return {"error": "rate_limit", "retry_after": e.reset_time}
        elif isinstance(e, requests.exceptions.RequestException):
            logger.error(f"API request failed in {context}: {e}")
            return {"error": "api_error", "message": str(e)}
        else:
            logger.error(f"Unexpected error in {context}: {e}")
            return {"error": "unknown", "message": str(e)}

    @staticmethod
    def handle_data_error(e: Exception, context: str) -> dict:
        """Handle data processing and storage errors"""
        if isinstance(e, json.JSONDecodeError):
            logger.error(f"Invalid JSON in {context}: {e}")
            return {"error": "invalid_json", "message": str(e)}
        elif isinstance(e, FileNotFoundError):
            logger.error(f"File not found in {context}: {e}")
            return {"error": "file_not_found", "message": str(e)}
        else:
            logger.error(f"Data error in {context}: {e}")
            return {"error": "data_error", "message": str(e)}

class CacheManager:
    """Manages caching of analysis results"""
    
    def __init__(self, cache_dir: str, max_age: timedelta = timedelta(hours=1)):
        self.cache_dir = cache_dir
        self.max_age = max_age
        self.cache = {}
        self.last_update = {}
        os.makedirs(cache_dir, exist_ok=True)
        
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired"""
        try:
            if key in self.cache:
                last_update = self.last_update.get(key)
                if last_update and datetime.now() - last_update < self.max_age:
                    return self.cache[key]
            return None
        except Exception as e:
            logger.error(f"Error retrieving from cache: {e}")
            return None
            
    def set(self, key: str, value: Any):
        """Set value in cache"""
        try:
            self.cache[key] = value
            self.last_update[key] = datetime.now()
            self._persist_cache()
        except Exception as e:
            logger.error(f"Error setting cache: {e}")
            
    def _persist_cache(self):
        """Persist cache to disk"""
        try:
            cache_file = os.path.join(self.cache_dir, 'analysis_cache.json')
            with open(cache_file, 'w') as f:
                json.dump({
                    'cache': self.cache,
                    'last_update': {k: v.isoformat() for k, v in self.last_update.items()}
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error persisting cache: {e}")

def ensure_directories():
    """Create necessary directories and initialize files if needed"""
    directories = [
        'tasks',
        'tasks/reduced_context',
        'data',
        'feedback'
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        logger.info(f"Ensured directory exists: {directory}")

def ensure_system_prompt():
    """Ensure system_prompt.txt exists with default content"""
    system_prompt_path = os.path.join('tasks', 'system_prompt.txt')
    if not os.path.exists(system_prompt_path):
        default_prompt = """You are an AI assistant helping to manage tasks and projects. When providing suggestions:

1. Always consider the user's character description and preferences
2. Break down large tasks into smaller, manageable steps
3. Prioritize administrative tasks that might be procrastinated
4. Provide clear, actionable next steps
5. Include specific timeframes and deadlines
6. Consider the optimal time of day for different task types
7. Balance technical work with administrative requirements

Remember to:
- Be proactive about administrative tasks
- Encourage regular breaks and task switching
- Provide structure and clear workflows
- Help maintain momentum across multiple projects"""

        with open(system_prompt_path, 'w') as f:
            f.write(default_prompt)
        logger.info("Created default system_prompt.txt")

@app.route('/api/project/<project_name>', methods=['GET', 'POST'])
def handle_project(project_name):
    """Handle project file operations"""
    try:
        tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
        file_path = os.path.join(tasks_dir, project_name)
        
        if request.method == 'GET':
            with open(file_path, 'r') as f:
                content = f.read()
            return jsonify({
                'success': True,
                'content': content
            })
        else:  # POST
            content = request.json.get('content')
            with open(file_path, 'w') as f:
                f.write(content)
            return jsonify({
                'success': True
            })
    except Exception as e:
        logger.error(f"Error handling project {project_name}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Handle file uploads with proper compression tracking"""
    try:
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file provided'
            }), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({
                'success': False,
                'error': 'No file selected'
            }), 400

        # Secure the filename and save
        filename = secure_filename(file.filename)
        tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
        file_path = os.path.join(tasks_dir, filename)
        
        # Save the original file
        file.save(file_path)
        
        # Initialize system components for compression
        system = initialize_system()
        
        # Attempt compression if needed
        compressed_path = system.context_aggregator.context_compressor.get_or_create_compressed_version(file_path)
        
        return jsonify({
            'success': True,
            'filename': filename,
            'compressed': compressed_path != file_path,
            'compressed_path': compressed_path if compressed_path != file_path else None
        })
        
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/analysis', methods=['GET'])
def get_analysis_report():
    """Get comprehensive analysis of task patterns"""
    try:
        # Initialize system components
        system = initialize_system()
        
        # Get latest tasks
        tasks = system.context_aggregator.github_monitor.get_tasks()
        task_list = tasks.get('tasks', [])
        
        # Perform analysis
        completion_patterns = system.task_analyzer.analyze_completion_patterns(task_list)
        temporal_patterns = system.temporal_analyzer.analyze_patterns(task_list)
        
        # Calculate velocity metrics
        velocity_data = system.task_analyzer.calculate_velocity_impact(task_list)
        
        # Format response
        analysis = {
            "timestamp": time.time(),
            "task_metrics": {
                "total_tasks": len(task_list),
                "completion_rate": completion_patterns.get('completion_rate', 0),
                "avg_completion_time": completion_patterns.get('avg_completion_time', 0),
                "recent_velocity": velocity_data.get('velocity_trend', {})
            },
            "temporal_insights": {
                "optimal_hours": temporal_patterns.get('optimal_times', {}),
                "day_patterns": temporal_patterns.get('day_patterns', {}),
                "recent_trends": temporal_patterns.get('recent_trends', [])
            },
            "success_patterns": {
                "common_blockers": completion_patterns.get('common_blockers', []),
                "successful_labels": completion_patterns.get('successful_labels', []),
                "completion_by_type": completion_patterns.get('completion_by_type', {})
            }
        }
        
        return jsonify({
            "success": True,
            "analysis": analysis
        })
        
    except Exception as e:
        logger.error(f"Error generating analysis report: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# Add this near the top of the file, after imports
_llm_service = None

def get_llm_service():
    """Get or create the singleton LLM service"""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service

def initialize_system():
    """Initialize the core system components"""
    try:
        # Use singleton LLM service
        llm_service = get_llm_service()
        
        # Initialize resource manager first
        resource_manager = GitHubResourceManager(
            github_token=os.getenv('GITHUB_TOKEN'),
            repo_name=os.getenv('GITHUB_REPO')
        )
        
        # Initialize components
        github_monitor = GitHubActivityMonitor(resource_manager)
        user_tracker = UserInteractionTracker()
        temporal_analyzer = TemporalAnalysis()
        task_analyzer = TaskAnalyzer()  # Add TaskAnalyzer
        feedback_collector = FeedbackCollector(github_monitor, user_tracker, temporal_analyzer)
        context_aggregator = ContextAggregator(github_monitor)
        
        return SimpleNamespace(
            resource_manager=resource_manager,
            github_monitor=github_monitor,
            user_tracker=user_tracker,
            temporal_analyzer=temporal_analyzer,
            task_analyzer=task_analyzer,  # Include in returned namespace
            feedback_collector=feedback_collector,
            context_aggregator=context_aggregator,
            llm_service=llm_service
        )
    except Exception as e:
        logger.error(f"Error initializing system: {e}")
        raise

def main():
    """Main entry point for the application"""
    try:
        # Ensure all required directories exist
        ensure_directories()
        
        # Initialize system components
        system = initialize_system()
        
        # Initialize scheduler for background tasks
        scheduler = BackgroundScheduler()
        
        # Add scheduled jobs
        scheduler.add_job(
            func=lambda: system.feedback_collector.update_feedback(),
            trigger='interval',
            hours=1,
            id='hourly_feedback_update'
        )
        
        scheduler.add_job(
            func=lambda: system.temporal_analyzer.analyze_patterns(
                system.github_monitor.get_tasks()
            ),
            trigger='interval',
            days=1,
            id='daily_pattern_analysis'
        )
        
        # Start the scheduler
        scheduler.start()
        
        # Start Flask app with port 5001 instead of 5000
        app.run(host='0.0.0.0', port=5001, debug=True)
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
        sys.exit(1)

@app.route('/')
def index():
    """Serve the main application page"""
    try:
        system = initialize_system()
        settings = {
            'current_provider': system.llm_service.provider
        }
        return render_template('index.html', settings=settings)
    except Exception as e:
        logger.error(f"Error serving index page: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Add this route for getting projects list
@app.route('/api/projects', methods=['GET'])
def get_projects():
    """Get list of all project files"""
    try:
        tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
        projects = [f for f in os.listdir(tasks_dir) 
                   if f.startswith('project_') and f.endswith('.txt')]
        
        return jsonify({
            'success': True,
            'projects': projects
        })
    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Add back the query endpoint
@app.route('/api/query', methods=['POST'])
def handle_query():
    """Handle incoming queries using the global system instance"""
    try:
        data = request.get_json()
        if not data or 'query' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing query in request'
            }), 400

        # Get repository contents including tasks and feedback
        system = initialize_system()
        repo_contents = system.github_monitor.get_tasks()
        
        # Debug logging for tasks
        logger.info("=== Tasks Debug Information ===")
        logger.info(f"Total tasks found: {len(repo_contents.get('tasks', []))}")
        
        # Log inbox tasks
        try:
            with open('tasks/inbox_tasks.json', 'r') as f:
                inbox_tasks = json.load(f)
                logger.info(f"Inbox tasks: {json.dumps(inbox_tasks, indent=2)}")
        except Exception as e:
            logger.error(f"Error reading inbox_tasks.json: {e}")
            
        # Log completed tasks
        try:
            with open('tasks/completed_tasks.json', 'r') as f:
                completed_tasks = json.load(f)
                logger.info(f"Completed tasks: {json.dumps(completed_tasks, indent=2)}")
        except Exception as e:
            logger.error(f"Error reading completed_tasks.json: {e}")
        
        # Aggregate context with feedback
        context = system.context_aggregator.aggregate_context(data['query'], repo_contents)
        
        # Debug logging for context
        logger.info("=== Context Debug Information ===")
        logger.info(f"Context being sent to LLM: {json.dumps(context, indent=2)}")
        
        # Generate suggestion using LLM
        suggestion = system.llm_service.generate_suggestion(context)
        logger.info(f"Generated suggestion: {suggestion}")
        
        # Collect feedback
        system.feedback_collector.collect_feedback(suggestion)

        return jsonify({
            'success': True,
            'suggestion': suggestion
        })
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/settings')
def settings_page():
    """Serve the settings page"""
    try:
        # Initialize system components
        system = initialize_system()
        
        # Get current LLM settings
        current_settings = {
            'current_provider': system.llm_service.provider,
            'available_providers': ['gemini', 'anthropic', 'openai'],
            'api_keys_configured': {
                'gemini': bool(os.getenv('GOOGLE_API_KEY')),
                'anthropic': bool(os.getenv('ANTHROPIC_API_KEY')),
                'openai': bool(os.getenv('OPENAI_API_KEY'))
            }
        }
        return render_template('settings.html', settings=current_settings)
    except Exception as e:
        logger.error(f"Error serving settings page: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/settings/provider', methods=['POST'])
def change_provider():
    """Change the LLM provider"""
    try:
        # Get the singleton LLM service
        llm_service = get_llm_service()
        
        # Get the requested provider from the request
        data = request.get_json()
        if not data or 'provider' not in data:
            return jsonify({
                'success': False,
                'error': 'No provider specified'
            }), 400
            
        new_provider = data['provider']
        
        # Try to switch the provider
        try:
            llm_service.set_provider(new_provider)
            logger.info(f"Successfully switched to provider: {new_provider}")
            return jsonify({
                'success': True,
                'provider': new_provider
            })
        except ValueError as e:
            logger.error(f"Failed to switch provider: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 400
            
    except Exception as e:
        logger.error(f"Error changing provider: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

class TaskAnalyzer:
    """Analyzes task completion patterns and metrics"""
    
    def __init__(self):
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
        self.cache = {}
        self.cache_timeout = timedelta(hours=1)
        self.last_update = None
        os.makedirs(self.data_dir, exist_ok=True)

    def calculate_velocity_impact(self, tasks: List[dict]) -> dict:
        """Calculate velocity trends and impact factors"""
        velocity_data = {
            "velocity_trend": {},
            "impact_factors": [],
            "recommendations": []
        }
        try:
            # Calculate current velocity
            current_velocity = self._calculate_velocity(tasks)
            
            # Analyze trend
            velocity_data["velocity_trend"] = {
                "current": current_velocity["tasks_per_day"],
                "trend": self._analyze_velocity_trend(tasks),
                "blockers": self._identify_velocity_blockers(tasks)
            }
            
            # Identify impact factors
            velocity_data["impact_factors"] = self._identify_impact_factors(tasks)
            
            # Generate recommendations
            velocity_data["recommendations"] = self._generate_velocity_recommendations(
                current_velocity, 
                velocity_data["impact_factors"]
            )
            
            return velocity_data
            
        except Exception as e:
            logger.error(f"Error calculating velocity impact: {e}")
            return velocity_data

    def _analyze_velocity_trend(self, tasks: List[dict]) -> str:
        """Analyze the trend in velocity over time"""
        try:
            completed_tasks = [t for t in tasks if t.get('completed_at')]
            if not completed_tasks:
                return "insufficient_data"

            # Group by week
            weekly_counts = {}
            for task in completed_tasks:
                completion_date = datetime.fromtimestamp(task['completed_at'])
                week_num = completion_date.isocalendar()[1]
                weekly_counts[week_num] = weekly_counts.get(week_num, 0) + 1

            # Calculate trend
            weeks = sorted(weekly_counts.keys())
            if len(weeks) < 2:
                return "stable"

            recent_velocity = weekly_counts[weeks[-1]]
            previous_velocity = sum(weekly_counts[w] for w in weeks[:-1]) / (len(weeks) - 1)

            if recent_velocity > previous_velocity * 1.2:
                return "increasing"
            elif recent_velocity < previous_velocity * 0.8:
                return "decreasing"
            return "stable"

        except Exception as e:
            logger.error(f"Error analyzing velocity trend: {e}")
            return "unknown"

    def _identify_velocity_blockers(self, tasks: List[dict]) -> List[dict]:
        """Identify factors blocking velocity"""
        try:
            blockers = []
            completed_tasks = [t for t in tasks if t.get('completed_at')]
            
            # Analyze completion times
            completion_times = []
            for task in completed_tasks:
                if task.get('created_at'):
                    time_taken = task['completed_at'] - task['created_at']
                    completion_times.append({
                        'time': time_taken,
                        'labels': task.get('labels', []),
                        'blockers': task.get('blockers', [])
                    })

            # Identify patterns in slow completions
            if completion_times:
                avg_time = sum(ct['time'] for ct in completion_times) / len(completion_times)
                slow_tasks = [ct for ct in completion_times if ct['time'] > avg_time * 1.5]
                
                # Analyze common factors in slow tasks
                common_labels = {}
                common_blockers = {}
                
                for task in slow_tasks:
                    for label in task['labels']:
                        common_labels[label] = common_labels.get(label, 0) + 1
                    for blocker in task['blockers']:
                        common_blockers[blocker] = common_blockers.get(blocker, 0) + 1

                # Add significant blockers
                for blocker, count in common_blockers.items():
                    if count >= len(slow_tasks) * 0.3:  # Present in 30% or more of slow tasks
                        blockers.append({
                            "type": "blocker",
                            "name": blocker,
                            "impact": count / len(slow_tasks)
                        })

                # Add problematic task types
                for label, count in common_labels.items():
                    if count >= len(slow_tasks) * 0.3:
                        blockers.append({
                            "type": "task_type",
                            "name": label,
                            "impact": count / len(slow_tasks)
                        })

            return sorted(blockers, key=lambda x: x["impact"], reverse=True)

        except Exception as e:
            logger.error(f"Error identifying velocity blockers: {e}")
            return []

    def _identify_impact_factors(self, tasks: List[dict]) -> List[dict]:
        """Identify factors impacting task completion velocity"""
        try:
            factors = []
            completed_tasks = [t for t in tasks if t.get('completed_at')]
            
            if not completed_tasks:
                return factors

            # Analyze time of day impact
            hour_success_rates = self._analyze_hourly_success_rates(completed_tasks)
            if hour_success_rates:
                factors.append({
                    "type": "time_of_day",
                    "data": hour_success_rates,
                    "impact_score": self._calculate_impact_score(hour_success_rates)
                })

            # Analyze day of week impact
            day_success_rates = self._analyze_daily_success_rates(completed_tasks)
            if day_success_rates:
                factors.append({
                    "type": "day_of_week",
                    "data": day_success_rates,
                    "impact_score": self._calculate_impact_score(day_success_rates)
                })

            # Sort by impact score
            return sorted(factors, key=lambda x: x["impact_score"], reverse=True)

        except Exception as e:
            logger.error(f"Error identifying impact factors: {e}")
            return []

    def _generate_velocity_recommendations(self, current_velocity: dict, impact_factors: List[dict]) -> List[str]:
        """Generate recommendations for improving velocity"""
        try:
            recommendations = []
            
            # Base recommendations on current velocity
            if current_velocity["tasks_per_day"] < 1:
                recommendations.append("Consider breaking down tasks into smaller, more manageable pieces")
            
            # Add recommendations based on impact factors
            for factor in impact_factors:
                if factor["type"] == "time_of_day":
                    optimal_hours = [h for h, r in factor["data"].items() if r > 0.7]
                    if optimal_hours:
                        recommendations.append(
                            f"Schedule important tasks during your most productive hours: {', '.join(map(str, optimal_hours))}"
                        )
                
                elif factor["type"] == "day_of_week":
                    optimal_days = [d for d, r in factor["data"].items() if r > 0.7]
                    if optimal_days:
                        recommendations.append(
                            f"Plan complex tasks for your most productive days: {', '.join(optimal_days)}"
                        )

            return recommendations

        except Exception as e:
            logger.error(f"Error generating velocity recommendations: {e}")
            return []

    def analyze_completion_patterns(self, tasks: List[dict]) -> dict:
        try:
            patterns = {
                "completion_rate": self._calculate_completion_rate(tasks),
                "velocity": self._calculate_velocity(tasks),
                "trends": self._analyze_trends(tasks),
                "blockers": self._identify_blockers(tasks)
            }
            
            # Log analysis results
            logger.info(f"Analyzed completion patterns: {json.dumps(patterns, indent=2)}")
            
            return patterns
        except Exception as e:
            logger.error(f"Error analyzing completion patterns: {e}")
            return {}
            
    def _calculate_completion_rate(self, tasks: List[dict]) -> float:
        """Calculate the task completion rate"""
        try:
            if not tasks:
                return 0.0
            completed = len([t for t in tasks if t.get('completed_at')])
            return completed / len(tasks)
        except Exception as e:
            logger.error(f"Error calculating completion rate: {e}")
            return 0.0
            
    def _calculate_velocity(self, tasks: List[dict]) -> dict:
        """Calculate task completion velocity metrics"""
        try:
            velocity = {
                "tasks_per_day": 0.0,
                "avg_completion_time": 0.0,
                "trend": "stable"
            }
            
            completed_tasks = [t for t in tasks if t.get('completed_at')]
            if not completed_tasks:
                return velocity
                
            # Calculate tasks per day
            date_counts = {}
            completion_times = []
            
            for task in completed_tasks:
                completion_date = datetime.fromtimestamp(task['completed_at']).date()
                date_counts[completion_date] = date_counts.get(completion_date, 0) + 1
                
                if task.get('created_at'):
                    completion_time = task['completed_at'] - task['created_at']
                    completion_times.append(completion_time)
            
            if date_counts:
                velocity["tasks_per_day"] = sum(date_counts.values()) / len(date_counts)
                
            if completion_times:
                velocity["avg_completion_time"] = sum(completion_times) / len(completion_times) / 3600  # Convert to hours
                
            return velocity
            
        except Exception as e:
            logger.error(f"Error calculating velocity: {e}")
            return {"tasks_per_day": 0.0, "avg_completion_time": 0.0, "trend": "unknown"}
            
    def _analyze_trends(self, tasks: List[dict]) -> List[dict]:
        """Analyze trends in task completion"""
        try:
            trends = []
            completed_tasks = [t for t in tasks if t.get('completed_at')]
            
            if not completed_tasks:
                return trends
                
            # Group by week
            weekly_stats = {}
            for task in completed_tasks:
                completion_date = datetime.fromtimestamp(task['completed_at'])
                week_num = completion_date.isocalendar()[1]
                
                weekly_stats.setdefault(week_num, {
                    "completed": 0,
                    "avg_time": 0.0,
                    "total_time": 0.0
                })
                
                weekly_stats[week_num]["completed"] += 1
                if task.get('created_at'):
                    completion_time = (task['completed_at'] - task['created_at']) / 3600
                    weekly_stats[week_num]["total_time"] += completion_time
            
            # Calculate averages and build trend data
            for week, stats in weekly_stats.items():
                if stats["completed"] > 0:
                    stats["avg_time"] = stats["total_time"] / stats["completed"]
                    trends.append({
                        "week": week,
                        "completed_count": stats["completed"],
                        "avg_completion_time": stats["avg_time"]
                    })
            
            return sorted(trends, key=lambda x: x["week"])
            
        except Exception as e:
            logger.error(f"Error analyzing trends: {e}")
            return []
            
    def _identify_blockers(self, tasks: List[dict]) -> List[dict]:
        """Identify common blockers and their impact"""
        try:
            blocker_stats = {}
            
            for task in tasks:
                blockers = task.get('blockers', [])
                for blocker in blockers:
                    blocker_stats.setdefault(blocker, {
                        "count": 0,
                        "avg_delay": 0.0,
                        "total_delay": 0.0
                    })
                    
                    blocker_stats[blocker]["count"] += 1
                    if task.get('completed_at') and task.get('created_at'):
                        delay = (task['completed_at'] - task['created_at']) / 3600
                        blocker_stats[blocker]["total_delay"] += delay
            
            # Calculate averages and sort by impact
            blockers = []
            for blocker, stats in blocker_stats.items():
                if stats["count"] > 0:
                    stats["avg_delay"] = stats["total_delay"] / stats["count"]
                    blockers.append({
                        "blocker": blocker,
                        "occurrence_count": stats["count"],
                        "avg_delay_hours": stats["avg_delay"]
                    })
            
            return sorted(blockers, key=lambda x: x["occurrence_count"], reverse=True)
            
        except Exception as e:
            logger.error(f"Error identifying blockers: {e}")
            return []

class RateLimitHandler:
    """Handles API rate limiting and retries"""
    
    def __init__(self, max_retries: int = 3, initial_delay: float = 1.0):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.retry_counts = {}
        
    def execute_with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function with retry logic for rate limits"""
        attempt = 0
        delay = self.initial_delay
        
        while attempt < self.max_retries:
            try:
                return func(*args, **kwargs)
                
            except RateLimitExceededException as e:
                attempt += 1
                if attempt == self.max_retries:
                    raise
                
                # Calculate delay with exponential backoff
                wait_time = delay * (2 ** (attempt - 1))
                logger.warning(f"Rate limit hit, waiting {wait_time}s before retry {attempt}")
                time.sleep(wait_time)
                
            except Exception as e:
                # Don't retry on non-rate-limit errors
                raise
                
    def track_request(self, endpoint: str):
        """Track request count for an endpoint"""
        now = time.time()
        self.retry_counts[endpoint] = self.retry_counts.get(endpoint, 0) + 1
        
    def should_retry(self, endpoint: str) -> bool:
        """Check if we should retry a request based on recent history"""
        count = self.retry_counts.get(endpoint, 0)
        return count < self.max_retries

if __name__ == '__main__':
    main()