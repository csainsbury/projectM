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
from typing import Optional, Dict, List, Any
import logging
import sys
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from time import sleep
from github import RateLimitExceededException
from werkzeug.utils import secure_filename

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
        try:
            tasks_content = {
                "tasks": [],
                "raw_contents": {}
            }

            logger.info("Starting task retrieval from GitHub")
            
            # Get all contents from the tasks directory
            contents = self.resource_manager.repo.get_contents("tasks")
            logger.info(f"Found {len(contents)} files in tasks directory")
            
            # Process all files in the tasks directory
            for content in contents:
                try:
                    logger.info(f"Processing file: {content.path}")
                    file_content = content.decoded_content.decode()
                    
                    # Handle JSON files
                    if content.path.endswith('.json'):
                        logger.info(f"Processing JSON file: {content.path}")
                        parsed = json.loads(file_content)
                        if isinstance(parsed, dict) and "tasks" in parsed:
                            tasks_content["tasks"].extend(parsed["tasks"])
                            logger.info(f"Added {len(parsed['tasks'])} tasks from {content.path}")
                        elif isinstance(parsed, list):
                            tasks_content["tasks"].extend(parsed)
                            logger.info(f"Added {len(parsed)} tasks from {content.path}")
                    
                    # Handle TXT files
                    elif content.path.endswith('.txt'):
                        logger.info(f"Processing TXT file: {content.path}")
                        # Add text files as tasks with their content
                        task = {
                            "id": os.path.splitext(os.path.basename(content.path))[0],
                            "type": "text",
                            "content": file_content,
                            "created_at": content.last_modified,
                            "filename": content.path
                        }
                        tasks_content["tasks"].append(task)
                        logger.info(f"Added text task from {content.path}")
                    
                    tasks_content["raw_contents"][content.path] = file_content
                    
                except Exception as e:
                    logger.error(f"Error processing {content.path}: {e}")
                    continue

            logger.info(f"Found {len(tasks_content['tasks'])} total tasks")
            logger.info(f"Task content sample: {str(tasks_content['tasks'][:1])}")
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
class TemporalAnalysis:
    """Analyzes time-based patterns in task completion and project phases"""
    
    def analyze_patterns(self, task_history: List[dict]) -> dict:
        """Analyze time-based patterns in task completion"""
        try:
            # Get basic temporal metrics
            basic_metrics = self._analyze_basic_patterns(task_history)
            
            # Find optimal suggestion times
            optimal_times = self._find_optimal_suggestion_times(task_history)
            
            # Analyze day patterns
            day_patterns = self._analyze_day_patterns(task_history)
            
            # Analyze project phase correlation
            phase_patterns = self._analyze_project_phase_correlation(task_history)
            
            return {
                **basic_metrics,
                "optimal_times": optimal_times,
                "day_patterns": day_patterns,
                "phase_patterns": phase_patterns
            }
        except Exception as e:
            logger.error(f"Error analyzing temporal patterns: {e}")
            return {}

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


# ---------------------------------------------------------------------------
# CLASS: FeedbackCollector
# ---------------------------------------------------------------------------
class FeedbackCollector:
    """Collects and analyzes feedback from multiple sources"""
    
    def __init__(self, github_monitor: GitHubActivityMonitor, user_tracker: UserInteractionTracker, temporal_analyzer: TemporalAnalysis):
        self.github_monitor = github_monitor
        self.user_tracker = user_tracker
        self.temporal_analyzer = temporal_analyzer

    def collect_feedback(self, suggestion_id: str) -> dict:
        """Collect comprehensive feedback for a suggestion"""
        try:
            # Get user interactions
            interactions = self.user_tracker.track_interactions(suggestion_id)
            
            # Get task history including completed tasks
            task_history = self.github_monitor.get_tasks().get("tasks", [])
            
            # Store completion information
            self._store_completion_data(task_history)
            
            # Calculate comprehensive metrics
            metrics = self.calculate_outcome_metrics(task_history, interactions)
            
            feedback_data = {
                "suggestion_id": suggestion_id,
                "timestamp": time.time(),
                "user_interactions": interactions,
                "metrics": metrics,
                "success_indicators": self.identify_success_patterns(task_history, interactions),
                "failure_patterns": self.identify_failure_patterns(task_history, interactions),
                "recommendations": self.generate_recommendations(task_history, interactions)
            }
            
            return feedback_data
        except Exception as e:
            logger.error(f"Error collecting feedback: {e}")
            return {}

    def _store_completion_data(self, task_history: List[dict]) -> None:
        """Store completion data in all relevant locations"""
        try:
            # Load completed tasks from tasks directory
            completed_tasks = []
            completed_file = os.path.join('tasks', 'completed_tasks.json')
            if os.path.exists(completed_file):
                with open(completed_file, 'r') as f:
                    completed_tasks = json.load(f)

            # Update success patterns
            success_patterns = {
                'completion_rate': len(completed_tasks) / len(task_history) if task_history else 0,
                'completed_count': len(completed_tasks),
                'last_updated': int(time.time())
            }
            with open('data/success_patterns.json', 'w') as f:
                json.dump(success_patterns, f, indent=2)

            # Update temporal analysis
            temporal_analysis = {
                'completions_by_hour': self._analyze_completions_by_hour(completed_tasks),
                'completions_by_day': self._analyze_completions_by_day(completed_tasks),
                'last_updated': int(time.time())
            }
            with open('data/temporal_analysis.json', 'w') as f:
                json.dump(temporal_analysis, f, indent=2)

            # Append to action outcomes
            with jsonlines.open('data/action_outcomes.jsonl', mode='a') as writer:
                for task in completed_tasks:
                    writer.write({
                        'task_id': task['id'],
                        'action': 'completed',
                        'timestamp': task['completed_at'],
                        'metadata': {
                            'completion_time': task['completed_at'] - task.get('created_at', task['completed_at']),
                            'labels': task.get('labels', []),
                            'priority': task.get('priority')
                        }
                    })

            logger.info(f"Updated all feedback stores with {len(completed_tasks)} completed tasks")

        except Exception as e:
            logger.error(f"Error storing completion data: {e}")
            raise

    def _analyze_completions_by_hour(self, completed_tasks: List[dict]) -> Dict[str, int]:
        """Analyze completions by hour of day"""
        hour_counts = {str(i): 0 for i in range(24)}
        for task in completed_tasks:
            if task.get('completed_at'):
                hour = datetime.fromtimestamp(task['completed_at']).hour
                hour_counts[str(hour)] += 1
        return hour_counts

    def _analyze_completions_by_day(self, completed_tasks: List[dict]) -> Dict[str, int]:
        """Analyze completions by day of week"""
        day_counts = {str(i): 0 for i in range(7)}
        for task in completed_tasks:
            if task.get('completed_at'):
                day = datetime.fromtimestamp(task['completed_at']).weekday()
                day_counts[str(day)] += 1
        return day_counts

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
    def __init__(self, github_monitor: GitHubActivityMonitor):
        self.github_monitor = github_monitor
        
    def aggregate_context(self, query: str, repo_contents: dict) -> dict:
        """Aggregate context from various sources for the LLM"""
        try:
            # Get current tasks
            tasks = repo_contents.get("tasks", [])
            
            # Load completed tasks
            completed_tasks = []
            completed_file = os.path.join('tasks', 'completed_tasks.json')
            if os.path.exists(completed_file):
                with open(completed_file, 'r') as f:
                    completed_tasks = json.load(f)
            
            logger.info(f"Aggregating context with {len(tasks)} current tasks and {len(completed_tasks)} completed tasks")
            
            # Load feedback data
            action_outcomes = self._load_feedback_data()
            success_patterns = self._load_success_patterns()
            temporal_analysis = self._load_temporal_analysis()
            
            # Build context dictionary with feedback and completed tasks
            context = {
                "user_query": query,
                "repo_info": {
                    "tasks": tasks,
                    "completed_tasks": completed_tasks,  # Add completed tasks to context
                    "feedback": {
                        "action_outcomes": action_outcomes,
                        "success_patterns": success_patterns,
                        "temporal_analysis": temporal_analysis
                    }
                }
            }
            
            logger.info(f"Context built with {len(tasks)} current tasks, {len(completed_tasks)} completed tasks, and feedback data")
            return context
            
        except Exception as e:
            logger.error(f"Error aggregating context: {e}")
            raise

    def _load_feedback_data(self) -> list:
        """Load action outcomes from feedback file"""
        try:
            outcomes = []
            feedback_path = os.path.join('feedback', 'action_outcomes.jsonl')
            if os.path.exists(feedback_path):
                with jsonlines.open(feedback_path) as reader:
                    for line in reader:
                        outcomes.append(line)
            return outcomes
        except Exception as e:
            logger.error(f"Error loading feedback data: {e}")
            return []

    def _load_success_patterns(self) -> dict:
        """Load success patterns from feedback"""
        try:
            patterns_path = os.path.join('feedback', 'success_patterns.json')
            if os.path.exists(patterns_path):
                with open(patterns_path, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading success patterns: {e}")
            return {}

    def _load_temporal_analysis(self) -> dict:
        """Load temporal analysis from feedback"""
        try:
            analysis_path = os.path.join('feedback', 'temporal_analysis.json')
            if os.path.exists(analysis_path):
                with open(analysis_path, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading temporal analysis: {e}")
            return {}


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
        """Construct a prompt from the context, call Gemini, and return an actionable suggestion."""
        try:
            # Get system prompt
            system_prompt = ""
            try:
                with open('tasks/system_prompt.txt', 'r') as f:
                    system_prompt = f.read().strip()
            except Exception as e:
                logger.error(f"Error reading system prompt: {e}")
                system_prompt = "Follow standard task prioritization and analysis protocols."

            user_query = context.get("user_query", "No user query provided.")
            tasks = context.get('repo_info', {}).get('tasks', [])
            completed_tasks = context.get('repo_info', {}).get('completed_tasks', [])
            feedback_data = context.get('repo_info', {}).get('feedback', {})
            
            prompt_text = f"""System Instructions:
{system_prompt}

User Query: {user_query}

Available Tasks:
{json.dumps(tasks, indent=2)}

Completed Tasks ({len(completed_tasks)} total):
{json.dumps(completed_tasks, indent=2)}

Historical Feedback:
Action Outcomes: {json.dumps(feedback_data.get('action_outcomes', []), indent=2)}
Success Patterns: {json.dumps(feedback_data.get('success_patterns', {}), indent=2)}
Temporal Analysis: {json.dumps(feedback_data.get('temporal_analysis', {}), indent=2)}

Please respond to the user query: "{user_query}" while following the system instructions above.
"""

            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt_text}
                        ]
                    }
                ]
            }

            response = requests.post(self.gemini_endpoint, json=payload)
            response_data = response.json()

            # Extract the text from Gemini's response
            if 'candidates' in response_data:
                for candidate in response_data['candidates']:
                    if 'content' in candidate:
                        content = candidate['content']
                        if 'parts' in content:
                            for part in content['parts']:
                                if 'text' in part:
                                    return part['text']

            # If we couldn't find the text in the expected structure, return the raw response
            logger.warning("Unexpected response structure from Gemini")
            return str(response_data)

        except Exception as e:
            logger.error(f"Error generating suggestion: {e}")
            return f"Error generating suggestion: {str(e)}"


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
        try:
            # Load completed tasks from completed_tasks.json
            completed_tasks = []
            completed_file = os.path.join('tasks', 'completed_tasks.json')
            if os.path.exists(completed_file):
                with open(completed_file, 'r') as f:
                    completed_tasks = json.load(f)
            
            # Calculate metrics including completed tasks
            total_tasks = len(task_history) + len(completed_tasks)
            completion_count = len(completed_tasks)
            
            # Calculate completion patterns
            analysis = {
                "completion_rate": completion_count / total_tasks if total_tasks > 0 else 0,
                "completed_count": completion_count,
                "total_count": total_tasks,
                "avg_completion_time": self._calculate_avg_completion_time(completed_tasks),
                "completion_by_priority": self._analyze_priority_patterns(completed_tasks),
                "completion_by_type": self._analyze_type_patterns(completed_tasks),
                "completion_by_label": self._analyze_label_patterns(completed_tasks),
                "recent_completions": self._get_recent_completions(completed_tasks, days=7)
            }
            
            logger.info(f"Analyzed {completion_count} completed tasks out of {total_tasks} total tasks")
            return analysis
            
        except Exception as e:
            logger.error(f"Error analyzing completion patterns: {e}")
            return {
                "completion_rate": 0,
                "completed_count": 0,
                "total_count": 0,
                "avg_completion_time": 0,
                "completion_by_priority": {},
                "completion_by_type": {},
                "completion_by_label": {},
                "recent_completions": []
            }

    def _get_recent_completions(self, completed_tasks: List[dict], days: int = 7) -> List[dict]:
        """Get tasks completed in the last N days"""
        cutoff = int((datetime.now() - timedelta(days=days)).timestamp())
        return [
            task for task in completed_tasks
            if task.get('completed_at', 0) > cutoff
        ]

    def _analyze_label_patterns(self, completed_tasks: List[dict]) -> Dict[str, int]:
        """Analyze completion patterns by label"""
        label_counts = {}
        for task in completed_tasks:
            for label in task.get('labels', []):
                label_counts[label] = label_counts.get(label, 0) + 1
        return label_counts

    def _calculate_avg_completion_time(self, completed_tasks: List[dict]) -> float:
        """Calculate average time to complete tasks"""
        completion_times = []
        for task in completed_tasks:
            if task.get('created_at') and task.get('completed_at'):
                completion_time = task['completed_at'] - task['created_at']
                completion_times.append(completion_time)
        
        return sum(completion_times) / len(completion_times) if completion_times else 0

    def calculate_velocity_impact(self, task_history: List[dict]) -> dict:
        """Calculate impact on project velocity"""
        try:
            # Calculate completion rate trend
            completion_rates = self._calculate_completion_rate_trend(task_history)
            
            # Analyze velocity over time
            velocity_trend = self._analyze_velocity_over_time(task_history)
            
            # Identify bottlenecks
            bottlenecks = self._identify_bottlenecks(task_history)
            
            return {
                'completion_rate_change': completion_rates,
                'velocity_trend': velocity_trend,
                'bottleneck_analysis': bottlenecks
            }
        except Exception as e:
            logger.error(f"Error calculating velocity impact: {e}")
            return {}

    def _calculate_completion_rate_trend(self, task_history: List[dict]) -> dict:
        """Calculate trend in task completion rates"""
        try:
            # Group tasks by week
            weeks = {}
            for task in task_history:
                if task.get('completed_at'):
                    week = datetime.fromtimestamp(task['completed_at']).isocalendar()[1]
                    weeks.setdefault(week, {'completed': 0, 'total': 0})
                    weeks[week]['completed'] += 1
                weeks.setdefault(week, {'completed': 0, 'total': 0})
                weeks[week]['total'] += 1
            
            # Calculate rates and trend
            rates = []
            for week in sorted(weeks.keys()):
                rate = weeks[week]['completed'] / weeks[week]['total'] if weeks[week]['total'] > 0 else 0
                rates.append({'week': week, 'rate': rate})
            
            return {
                'weekly_rates': rates,
                'trend': self._calculate_trend(rates)
            }
        except Exception as e:
            logger.error(f"Error calculating completion rate trend: {e}")
            return {}

    def _analyze_velocity_over_time(self, task_history: List[dict]) -> dict:
        """Analyze how velocity changes over time"""
        try:
            # Track completed tasks per day
            daily_velocity = {}
            for task in task_history:
                if task.get('completed_at'):
                    day = datetime.fromtimestamp(task['completed_at']).date().isoformat()
                    daily_velocity.setdefault(day, 0)
                    daily_velocity[day] += 1
            
            # Calculate moving average
            window_size = 7
            moving_avg = []
            days = sorted(daily_velocity.keys())
            for i in range(len(days) - window_size + 1):
                window = [daily_velocity[days[j]] for j in range(i, i + window_size)]
                avg = sum(window) / window_size
                moving_avg.append({'date': days[i + window_size - 1], 'velocity': avg})
            
            return {
                'daily_velocity': [{'date': k, 'tasks': v} for k, v in daily_velocity.items()],
                'moving_average': moving_avg
            }
        except Exception as e:
            logger.error(f"Error analyzing velocity: {e}")
            return {}

    def _identify_bottlenecks(self, task_history: List[dict]) -> dict:
        """Identify patterns that slow down task completion"""
        try:
            bottlenecks = {
                'dependencies': {},
                'duration': {},
                'status_transitions': {}
            }
            
            for task in task_history:
                # Analyze dependency chains
                if task.get('depends_on'):
                    for dep in task['depends_on']:
                        bottlenecks['dependencies'].setdefault(dep, 0)
                        bottlenecks['dependencies'][dep] += 1
                
                # Analyze task duration
                if task.get('completed_at') and task.get('created_at'):
                    duration = task['completed_at'] - task['created_at']
                    category = self._categorize_duration(duration)
                    bottlenecks['duration'].setdefault(category, 0)
                    bottlenecks['duration'][category] += 1
                
                # Analyze status transitions
                if task.get('status_history'):
                    for i in range(len(task['status_history']) - 1):
                        transition = f"{task['status_history'][i]} -> {task['status_history'][i+1]}"
                        bottlenecks['status_transitions'].setdefault(transition, 0)
                        bottlenecks['status_transitions'][transition] += 1
            
            return bottlenecks
        except Exception as e:
            logger.error(f"Error identifying bottlenecks: {e}")
            return {}

    @staticmethod
    def _calculate_trend(data: List[dict]) -> float:
        """Calculate linear trend from time series data"""
        try:
            if not data:
                return 0
            x = list(range(len(data)))
            y = [item['rate'] for item in data]
            
            # Simple linear regression
            n = len(data)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x_i * y_i for x_i, y_i in zip(x, y))
            sum_xx = sum(x_i * x_i for x_i in x)
            
            slope = (n * sum_xy - sum_x * sum_y) / (n * sum_xx - sum_x * sum_x)
            return slope
        except Exception as e:
            logger.error(f"Error calculating trend: {e}")
            return 0

    @staticmethod
    def _categorize_duration(duration: float) -> str:
        """Categorize task duration into buckets"""
        if duration < 3600:  # 1 hour
            return "quick"
        elif duration < 86400:  # 1 day
            return "medium"
        elif duration < 604800:  # 1 week
            return "long"
        else:
            return "extended"


# ---------------------------------------------------------------------------
# CLASS: ActionSuggestionSystem
# ---------------------------------------------------------------------------
class ActionSuggestionSystem:
    """Main application class for processing user queries and updating feedback."""
    
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

    def process_query(self, query: str) -> str:
        """Process a user query and return a suggestion"""
        try:
            # Get repository contents
            repo_contents = self.context_aggregator.github_monitor.get_tasks()
            
            # Aggregate context with feedback
            context = self.context_aggregator.aggregate_context(query, repo_contents)
            
            # Generate suggestion
            suggestion = self.llm_service.generate_suggestion(context)
            
            return suggestion
        except Exception as e:
            logger.error(f"Error processing query: {e}")
            raise

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
        # Create resource manager first
        resource_manager = GitHubResourceManager(GITHUB_TOKEN, GITHUB_REPO)
        
        # Create GitHub monitor with resource manager
        github_monitor = GitHubActivityMonitor(resource_manager)
        current_tasks = github_monitor.get_tasks().get("tasks", [])
        
        # Load previous tasks from feedback repository
        feedback_repo = FeedbackRepository(GITHUB_TOKEN, GITHUB_REPO)
        
        # Ensure previous_tasks.json exists
        feedback_repo.ensure_file_exists(f"{FEEDBACK_DIR}/previous_tasks.json", "[]")
        
        try:
            previous_tasks_content = feedback_repo.repo.get_contents(f"{FEEDBACK_DIR}/previous_tasks.json")
            previous_tasks = json.loads(previous_tasks_content.decoded_content.decode())
        except Exception as e:
            logger.warning(f"Could not load previous tasks, using empty list: {e}")
            previous_tasks = []
        
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
        # Create resource manager first
        resource_manager = GitHubResourceManager(GITHUB_TOKEN, GITHUB_REPO)
        
        # Create GitHub monitor with resource manager
        github_monitor = GitHubActivityMonitor(resource_manager)
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
# Main initialization
# ---------------------------------------------------------------------------
def initialize_system() -> 'ActionSuggestionSystem':
    """Initialize the complete system with all required components"""
    try:
        # Initialize GitHub components
        resource_manager = GitHubResourceManager(GITHUB_TOKEN, GITHUB_REPO)
        github_monitor = GitHubActivityMonitor(resource_manager)
        
        # Initialize other components
        context_aggregator = ContextAggregator(github_monitor)
        llm_service = LLMService()
        feedback_repo = FeedbackRepository(GITHUB_TOKEN, GITHUB_REPO)
        feedback_collector = FeedbackCollector(
            github_monitor, 
            UserInteractionTracker(), 
            TemporalAnalysis()
        )
        
        # Create and return the main system
        return ActionSuggestionSystem(
            llm_service=llm_service,
            context_aggregator=context_aggregator,
            feedback_repo=feedback_repo,
            feedback_collector=feedback_collector,
            task_analyzer=TaskAnalysisService(),
            user_tracker=UserInteractionTracker(),
            temporal_analyzer=TemporalAnalysis()
        )
    except Exception as e:
        logger.error(f"Error initializing system: {e}")
        raise


def ensure_directories():
    """Ensure all required directories and files exist"""
    try:
        # Create directories if they don't exist
        os.makedirs('data', exist_ok=True)
        os.makedirs('feedback', exist_ok=True)
        
        # Initialize empty files if they don't exist
        default_files = {
            'data/action_outcomes.jsonl': '',
            'data/success_patterns.json': '{}',
            'data/temporal_analysis.json': '{}',
            'feedback/completed_tasks.jsonl': ''
        }
        
        for filepath, default_content in default_files.items():
            if not os.path.exists(filepath):
                with open(filepath, 'w') as f:
                    f.write(default_content)
                logger.info(f"Created file: {filepath}")
                
        logger.info("Directory structure initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing directories: {e}")
        raise


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder='static')
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.route('/')
def index():
    """Serve the main HTML interface"""
    return render_template('index.html')

@app.route('/api/query', methods=['POST'])
def handle_query():
    """Handle API requests from the frontend"""
    try:
        logger.info("Received query request")
        data = request.json
        logger.info(f"Request data: {data}")
        
        query = data.get('query', '')
        
        # Initialize system
        try:
            system = initialize_system()
            logger.info("System initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize system: {e}")
            return jsonify({
                'success': False,
                'error': 'System initialization failed'
            }), 500

        try:
            # Get repository contents including tasks and feedback
            repo_contents = system.context_aggregator.github_monitor.get_tasks()
            logger.info(f"Retrieved {len(repo_contents.get('tasks', []))} tasks from GitHub")
            
            # Aggregate context with feedback
            context = system.context_aggregator.aggregate_context(query, repo_contents)
            logger.info("Context aggregated successfully")
            
            # Log the context being sent to LLM
            logger.info(f"Sending context to LLM with {len(context['repo_info']['tasks'])} tasks")
            
            # Generate suggestion using LLM
            suggestion = system.llm_service.generate_suggestion(context)
            logger.info("Generated suggestion successfully")
            
            return jsonify({
                'success': True,
                'suggestion': suggestion
            })
        except Exception as e:
            logger.error(f"Error processing query: {str(e)}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
            
    except Exception as e:
        logger.error(f"Error handling request: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500

# Add this function to handle periodic GitHub updates
def sync_local_to_github(file_pattern='inbox'):
    """Sync local task files to GitHub repository"""
    try:
        logger.info(f"Starting sync with pattern: {file_pattern}")
        github_token = os.getenv('GITHUB_TOKEN')
        if not github_token:
            raise ValueError("GITHUB_TOKEN not found in environment variables")
            
        github = Github(github_token)
        repo = github.get_repo(GITHUB_REPO)
        logger.info(f"Connected to GitHub repo: {GITHUB_REPO}")
        
        # Get absolute path to tasks directory
        tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
        logger.info(f"Tasks directory: {tasks_dir}")
        
        if not os.path.exists(tasks_dir):
            raise ValueError(f"Tasks directory not found: {tasks_dir}")
        
        # Determine which files to sync
        files_to_sync = []
        if file_pattern == 'inbox':
            files_to_sync = ['inbox_tasks.json']
            logger.info("Syncing inbox tasks only")
        else:
            files_to_sync = [f for f in os.listdir(tasks_dir) 
                           if f.endswith(('.json', '.txt'))]
            logger.info(f"Found {len(files_to_sync)} files to sync: {files_to_sync}")
        
        # Add feedback files to sync
        if file_pattern == 'all':
            files_to_sync.extend([
                'data/action_outcomes.jsonl',
                'data/success_patterns.json',
                'data/temporal_analysis.json',
                'feedback/completed_tasks.jsonl'
            ])
        
        for filename in files_to_sync:
            max_retries = 3
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    # Check rate limit before each operation
                    rate_limit = github.get_rate_limit()
                    if rate_limit.core.remaining < 2:
                        wait_time = (rate_limit.core.reset - datetime.now()).seconds + 60
                        logger.info(f"Rate limit reached. Waiting {wait_time} seconds...")
                        sleep(wait_time)
                    
                    # Read local file
                    file_path = os.path.join(tasks_dir, filename)
                    logger.info(f"Reading local file: {file_path}")
                    
                    if not os.path.exists(file_path):
                        logger.warning(f"File not found: {file_path}")
                        break
                    
                    # Read file content based on type
                    try:
                        if filename.endswith('.json'):
                            with open(file_path, 'r') as f:
                                file_content_str = json.dumps(json.load(f), indent=2)
                        else:  # .txt files
                            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                                file_content_str = f.read()
                    except UnicodeDecodeError:
                        logger.error(f"Unicode decode error in {filename}, trying with 'latin-1' encoding")
                        with open(file_path, 'r', encoding='latin-1') as f:
                            file_content_str = f.read()
                    
                    logger.info(f"Successfully read {filename}")
                    
                    # Try to get existing file from GitHub
                    github_path = f"tasks/{filename}"
                    logger.info(f"Checking for existing file in GitHub: {github_path}")
                    
                    try:
                        file_content = repo.get_contents(github_path)
                        current_content = file_content.decoded_content.decode('utf-8')
                        
                        # Compare contents
                        if current_content == file_content_str:
                            logger.info(f"No changes detected for {filename}, skipping update")
                            break
                        
                        logger.info(f"Changes detected, updating {github_path}")
                        result = repo.update_file(
                            github_path,
                            f"Update {filename} from local system",
                            file_content_str,
                            file_content.sha
                        )
                        logger.info(f"Successfully updated {filename} in GitHub")
                    except Exception as not_found:
                        logger.info(f"File not found in GitHub, creating new: {github_path}")
                        result = repo.create_file(
                            github_path,
                            f"Initial upload of {filename}",
                            file_content_str
                        )
                        logger.info(f"Successfully created {filename} in GitHub")
                    
                    logger.info(f"Sync complete for {filename}")
                    sleep(1)
                    break
                    
                except RateLimitExceededException:
                    rate_limit = github.get_rate_limit()
                    wait_time = (rate_limit.core.reset - datetime.now()).seconds + 60
                    logger.warning(f"Rate limit exceeded. Waiting {wait_time} seconds...")
                    sleep(wait_time)
                    retry_count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing {filename}: {str(e)}")
                    retry_count += 1
                    if retry_count < max_retries:
                        sleep(5)
                    else:
                        logger.error(f"Failed to sync {filename} after {max_retries} attempts")
                        break
                
    except Exception as e:
        logger.error(f"Failed to initialize GitHub sync: {str(e)}")
        raise

@app.route('/api/projects', methods=['GET'])
def get_projects():
    """Get list of project files"""
    try:
        tasks_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tasks')
        project_files = [f for f in os.listdir(tasks_dir) 
                        if f.startswith('project_') and f.endswith('.txt')]
        return jsonify({
            'success': True,
            'projects': project_files
        })
    except Exception as e:
        logger.error(f"Error getting projects: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

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
    """Handle file uploads to tasks directory"""
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
        
        # Save the file
        file.save(file_path)
        
        logger.info(f"Successfully uploaded file: {filename}")
        return jsonify({
            'success': True,
            'filename': filename
        })
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == "__main__":
    try:
        print("Initializing ProjectM system...")
        ensure_directories()
        
        # Initialize the system once at startup
        system = initialize_system()
        print("System initialized successfully")
        
        print("Starting ProjectM server...")
        # Run the Flask app with debug mode and allow all hosts
        app.run(debug=True, host='0.0.0.0', port=5001)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)