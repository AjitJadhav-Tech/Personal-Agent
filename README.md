# Personal Agent

A LangGraph-based personal AI agent for job search (tailored resume and cover letter per job description), LinkedIn personal brand building, and daily email activity reports.

See [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md) for the agent design and strategy, and [docs/AGENTCORE_ARCHITECTURE.md](docs/AGENTCORE_ARCHITECTURE.md) for the serverless AWS architecture, human-in-the-loop approvals and deployment.

## Status

Web app (`/app`, Cognito sign-in): approvals, post requests, run now, pause, activity, and opportunity replies (paste a message, get a fact-checked reply draft to send yourself).

Phase 3 is built: after you connect LinkedIn once, approved posts are published through the official Posts API (when `dryRun` is `"false"`).

Phase 1 is built: jobs from Greenhouse, Ashby and Lever boards are scored by Kimi K3, and each job you approve gets a fact-checked, tailored resume and cover letter by email. Phase 0 (foundation) underneath it: LangGraph workflows for posts, job search and the daily report, each pausing for your approval by email; the AgentCore Runtime entry point; the approval page; and the full serverless stack in AWS CDK for us-east-1. It runs in dry-run mode (nothing is published) until you turn that off.

- Deploy (CodeBuild pipeline, model Kimi K3 on Bedrock): [docs/DEPLOY.md](docs/DEPLOY.md)
- Your settings: [profile/preferences.yaml](profile/preferences.yaml)
- Tests: `uv venv && uv pip install -e ".[dev]" && .venv/bin/python -m pytest`
