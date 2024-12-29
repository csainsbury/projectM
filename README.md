Multi-Project Task Management Engine

This repository provides a Python-based application for multi-project task management, feedback collection, and analytics. It integrates with GitHub for task storage, utilizes a Large Language Model (Gemini), and offers automated suggestion and feedback workflows.

The main components include:
	•	GitHub Integration for storing tasks, feedback, and analytics data.
	•	LLM Service (Gemini) for generating action suggestions, analyzing tasks, and providing context-aware recommendations.
	•	Task Monitoring & Analysis modules that track changes to tasks, store feedback, and identify completion/failure patterns over time.
	•	API Server (Flask) to expose endpoints for querying tasks, uploading new tasks, and retrieving project-level data.

Table of Contents
	1.	Features
	2.	High-Level Architecture
	3.	Installation
	4.	Configuration
	5.	User Guide
	6.	Usage
	7.	Contributing
	8.	License

Features
	•	GitHub Integration: Automatically reads tasks from a GitHub repository (tasks directory), monitors changes, and updates feedback data in JSON/JSONL files.
	•	Feedback & Analytics: Logs user interactions, calculates completion rates, analyzes daily/weekly patterns, identifies bottlenecks, and updates feedback files in real time.
	•	LLM Suggestions: Sends aggregated context to the Gemini API for next-action suggestions, including relevant task data, interaction logs, and success/failure patterns.
	•	Flask API: Offers a RESTful interface for third-party tools or front-end clients to manage tasks, upload files, and retrieve suggestions from the LLM.
	•	Cron-Like Jobs: hourly_task_sync and daily_pattern_analysis tasks can be scheduled to maintain up-to-date feedback and analytics data in the GitHub repo.

High-Level Architecture

                         +-------------------------+
                         |     GitHub Repository   |
                         | (Stores tasks/feedback) |
                         +-----------+-------------+
                                     |
                                     v
         +-------------------+   +-------+  +-----------------------+
         | Task Monitoring   +<--| LLM   |  | Feedback & Analytics  |
         | & Analysis        |   | (Gemini) | +--- Aggregates data --+
         +-------------------+   +-------+  +-----------------------+
                                     |
                                     v
                            +-----------------+
                            |   Flask API     |
                            |   (ProjectM)    |
                            +--------+--------+
                                     |
                              (User / Frontend)

	1.	GitHub Repository: Houses .json and .txt task files and feedback/ data.
	2.	Task Monitoring & Analysis: Gathers newly created or updated tasks, merges them with historical data, and identifies completed, modified, and downstream-dependent tasks.
	3.	LLM Service (Gemini): Provides language-generation capabilities for task suggestions and context-sensitive analysis.
	4.	Feedback & Analytics: Collects user interaction data, calculates metrics (completion rate, average time to complete tasks, success/failure patterns, etc.).
	5.	Flask API: Exposes endpoints for local usage or external consumption, providing functionality such as uploading tasks, querying suggestions, and retrieving project files.

Installation
	1.	Clone the Repository

git clone https://github.com/<username>/projectM.git
cd projectM


	2.	Create and Activate a Virtual Environment (Optional but Recommended)

python3 -m venv venv
source venv/bin/activate

Or on Windows:

python -m venv venv
.\venv\Scripts\activate


	3.	Install Dependencies
Make sure you have Python 3.7+ installed, then:

pip install -r requirements.txt


	4.	Create a .env File
Copy .env.example to .env (if provided) or create your own .env file with the variables:

GITHUB_TOKEN=<YOUR_GITHUB_PERSONAL_ACCESS_TOKEN>
GOOGLE_API_KEY=<YOUR_GEMINI_API_KEY>

Adjust other environment variables as needed.

	5.	Initialize Directory Structure
Make sure the data/ and feedback/ directories exist. If not, they will be created automatically when the server starts:

python3 <your_script>.py

(Where <your_script>.py is the main entry file, e.g., app.py or the provided #!/usr/bin/env python3 script above.)

User Guide

Below is a quick guide to setting up and using the system in your own local environment:
	1.	Set Up Environment
	•	Ensure Python 3.7+ is installed on your machine.
	•	Install required Python packages using pip install -r requirements.txt.
	•	Create a .env file containing valid GITHUB_TOKEN and GOOGLE_API_KEY values (see Configuration).
	2.	Prepare Data and Tasks
	•	By default, the system looks in tasks/ for your .json or .txt files.
	•	You can place any files you want to be treated as tasks in this directory, for example:

tasks/inbox_tasks.json
tasks/project_alpha.json
tasks/notes.txt


	•	If feedback/completed_tasks.jsonl or other feedback-related files do not exist, they will be initialized automatically.

	3.	Run the Server
	•	Launch the Flask server to expose the API and run the background processes:

python3 <your_script>.py


	•	By default, the app runs on localhost:5001.
	•	Check logs for “System initialized successfully” or any error messages.

	4.	Using the API
	•	POST /api/query – Accepts a JSON body with a "query" field. This triggers an LLM call to Gemini based on your tasks, feedback, and user query. Returns a suggestion from the model.
	•	GET /api/projects – Returns a list of project files.
	•	GET /api/project/<project_name> – Retrieves the content of a specific project file.
	•	POST /api/project/<project_name> – Updates the content of a project file.
	•	POST /api/upload – Uploads a new file to the tasks/ directory.
	•	Endpoints can be tested with curl, Postman, or any other REST client.
	5.	Maintaining Feedback and Analytics
	•	Hourly & Daily Jobs:
	•	hourly_task_sync() – Syncs local tasks and updates GitHub with any changes.
	•	daily_pattern_analysis() – Analyzes task patterns (like completion rates and temporal data) and updates feedback files.
	•	These can be integrated into cron jobs or scheduled tasks.
	6.	Extending the System
	•	You can customize the LLMService to integrate with other models or local generative AI backends.
	•	Modify TaskAnalysisService and FeedbackCollector to add domain-specific metrics or patterns.
	•	Adjust ActionSuggestionSystem to fit your project’s unique workflow, e.g., automatically assigning tasks, sending notifications, etc.

Usage
	1.	Local Development
	•	Start the Flask server:

python3 <your_script>.py


	•	Access the UI (if you have an index.html in templates/) by navigating to http://localhost:5001/.

	2.	Testing with cURL

curl -X POST -H "Content-Type: application/json" \
     -d '{"query":"What should I do next on project Alpha?"}' \
     http://localhost:5001/api/query

Expect a JSON response containing a "suggestion" key with Gemini’s recommended next steps.

	3.	Periodic Sync & Analysis (Optional)
	•	In production setups, schedule these functions:

from your_script import hourly_task_sync, daily_pattern_analysis
# e.g., with crontab or a separate scheduling script
hourly_task_sync(...)
daily_pattern_analysis(...)


	•	These keep your tasks, feedback, and analytics in sync with the GitHub repo.

Contributing

Pull requests are welcome! If you’d like to report a bug or request a feature, please open an issue. For major changes, please discuss them first to ensure they align with the project roadmap.

License

This project is open-sourced under an MIT license. See the LICENSE file for details.