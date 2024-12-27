===
# ProjectM: LLM-Driven Multi-Project Management

This repository contains a Python-based application that uses an LLM (currently via the Google Gemini model) to provide real-time suggestions and next-best-action recommendations for ongoing projects. It integrates with GitHub for storing feedback, user interactions, and project data in JSON or JSONL files. It also retrieves tasks from external sources (like Todoist) or from locally stored task files.

## Key Features

- **LLM Integration (Gemini)**: The core logic sends prompts to Google's Gemini model to generate suggestions on task prioritization, completion patterns, and next steps.
- **GitHub as Data Storage**: The app writes feedback, success patterns, and temporal analysis data into JSON/JSONL files stored under a `feedback` directory. These can be versioned in GitHub as needed.
- **Task Management**: The system can pull tasks from local JSON files or from third-party services (like Todoist), comparing them to previous states to track completions, modifications, and emerging priorities.
- **Proactive Feedback Loop**: The application updates its feedback with user interactions, temporal analysis, and project velocity data, then integrates these insights into future suggestions.
- **Scheduled Jobs**: Includes `hourly_task_sync` and `daily_pattern_analysis` functions that periodically refresh tasks, evaluate patterns, and update the feedback files.

## High-Level Architecture

1. **`projectM.py`**: Central engine that defines classes and orchestrates the main flow:
   - **`ActionSuggestionSystem`**: Orchestrates user query handling and LLM suggestions.
   - **`LLMService`**: Manages the Gemini model call, constructing and sending prompts, then parsing the response.
   - **`ContextAggregator`**: Pulls the latest context from local files and merges it with user queries.
   - **`TaskAnalysisService`, `TemporalAnalysis`, `FeedbackCollector`, `FeedbackRepository`**: Collate patterns from tasks, user interactions, and time-based data, saving these to JSON or JSONL.

2. **Feedback & Patterns**: The `feedback` directory contains files like:
   - **`action_outcomes.jsonl`**: Appends new outcomes or user interactions in JSONL format.
   - **`success_patterns.json`**: Summarizes success metrics (e.g., success rate, average completion time).
   - **`temporal_analysis.json`**: Stores time-based insights on completions by hour, day, etc.

3. **Usage**:
   - **Start the server**:
     ```bash
     ./start_server.sh
     ```
     This runs a small Flask server (default on port 5001).
   - **Sync tasks**:
     ```bash
     ./sync_inbox.sh
     ```
     This pulls updated tasks from the specified JSON file (or from an external service if configured).
   - **Interact with the app**: 
     - Navigate to [http://localhost:5001](http://localhost:5001) in your browser.
     - Type a query in the text area (e.g. “What tasks should I focus on this week?”). 
     - The app processes your query and returns an LLM-generated suggestion.

4. **Cron-like Jobs** (Optional):
   - **Hourly task sync**: Use `hourly_task_sync` to automatically refresh tasks and update feedback.
   - **Daily pattern analysis**: Use `daily_pattern_analysis` to examine deeper metrics like success rate, optimal times to complete tasks, etc.

5. **Configuration Notes**:
   - **Environment Variables**: 
     - `GITHUB_TOKEN` is used to authenticate GitHub calls (if you want to push changes to a GitHub repository). 
     - `GOOGLE_API_KEY` is used by the `LLMService` to call the Gemini endpoint.
   - **Paths**:
     - Most local data references reside in `tasks/` and `feedback/`. 
     - `start_server.sh` sets up a conda environment before running.
   - **Dependencies**: 
     - Ensure you have installed the required Python packages (`requirements.txt` not included by default, but recommended to install `requests`, `flask`, `PyGithub`, `python-dotenv`, etc.).

## Contributing

Feel free to open issues or pull requests if you want to extend the system. Ensure you follow the established code and feedback structure. For significant feature changes, please discuss them in an issue first.

## License

This project is available under a license specified by the repository’s owner. Please see [LICENSE](LICENSE) for details (if present).

===    
    </content>
  </change>
</file>