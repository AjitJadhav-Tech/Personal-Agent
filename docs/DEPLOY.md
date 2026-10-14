# Deploying Personal Agent (phase 0)

Region: **us-east-1**. Approval requests, the daily report, alarms and budget alerts all go to **ajit.jadhav.ai@outlook.com** (set in `infra/cdk.json`).

Phase 0 runs in **dry-run mode**: it drafts posts, finds jobs, emails you for approval and records your decisions, but never publishes anything. LinkedIn publishing arrives in phase 3.

## 1. One-time AWS setup

1. An AWS account with admin access for the first deploy, and the AWS CLI signed in (`aws sts get-caller-identity`).
2. **Bedrock model access**: in the Bedrock console (us-east-1), open *Model catalog* and make sure the Anthropic Claude Sonnet and Haiku models are enabled for your account (first use asks for a short use-case form).
3. **AgentCore Observability**: in CloudWatch, enable *Transaction Search* once (CloudWatch → Application Signals → Transaction search). Agent traces then appear in the CloudWatch *GenAI Observability* dashboard.
4. Tools on your machine: Python 3.12+, Node.js 22 (`npm i -g aws-cdk`), and Docker with ARM64 build support (Docker Desktop has it; on Linux use `docker buildx` with QEMU).

## 2. Deploy

```bash
uv venv && uv pip install -e ".[dev]"      # or: python -m venv .venv && pip install -e ".[dev]"
source .venv/bin/activate
cd infra
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1  # first time only
cdk deploy
```

The deploy prints `ApprovalApiUrl`, `AgentRuntimeArn`, `MemoryId` and a ready-to-run test command.

## 3. Confirm three emails

Check the Outlook inbox (and Junk) for:

1. **Amazon SES verification**: click the link. Until you do, the agent cannot email you.
2. **AWS Notifications subscription confirmation**: click *Confirm subscription* so failure alarms reach you.
3. Budget alerts need no confirmation.

SES starts in sandbox mode, which only allows sending to verified addresses. That is fine here because the agent only ever emails you.

**Deliverability note:** the emails are sent *from* your Outlook address through SES. Outlook may file them in Junk because they don't come from Microsoft's servers. Mark the first one *Not junk* and add the address to Safe senders. For the most reliable delivery later, verify a domain you own in SES and set `"senderEmail": "agent@yourdomain.com"` in `infra/cdk.json`.

## 4. Fill in your preferences

Edit `profile/preferences.yaml` (target titles, skills, locations, Greenhouse boards of companies you want to watch, your positioning sentence), commit, and run `cdk deploy` again.

## 5. Try it

```bash
# Draft a post and email it to you for approval
aws lambda invoke --function-name personal-agent-runner \
  --payload '{"task":"content"}' --cli-binary-format raw-in-base64-out out.json

# Fetch and shortlist jobs, then email the batch for approval
aws lambda invoke --function-name personal-agent-runner \
  --payload '{"task":"job_hunt"}' --cli-binary-format raw-in-base64-out out.json
```

Open the email, click **Review and decide**, then approve, edit or reject. The page only records a decision when you press a button (opening the link does nothing, so Outlook's link scanning can't approve anything). Each link works once and expires (posts after 48 hours, job batches after 7 days).

## 6. Schedules (Asia/Kolkata)

| Workflow | When |
|---|---|
| Job search | 08:00, 13:00, 18:00 daily |
| Post draft for approval | Tuesday and Thursday 07:30 |
| Daily report | 20:00 daily |

Change the timezone in `infra/cdk.json` and the times in `SCHEDULES` in `infra/stacks/personal_agent_stack.py`.

## 7. Operating it

- **Pause everything:** `aws ssm put-parameter --name /personal-agent/paused --value true --overwrite` (set back to `false` to resume). If the flag can't be read, the agent treats itself as paused.
- **Logs and traces:** CloudWatch → GenAI Observability → Bedrock AgentCore; Lambda logs under `/aws/lambda/personal-agent-runner` and the approval function.
- **Cost guard:** an AWS Budget emails you at 80% of `monthlyBudgetUsd` (default $50), actual or forecast.
- **Remove:** `cdk destroy`. The DynamoDB table is retained on purpose; delete it manually if you want the history gone.

## What's not in phase 0 yet

- Resume and cover-letter tailoring with the fact check (phase 1)
- LinkedIn sign-in through AgentCore Identity and publishing via the Posts API (phase 3); `dryRun` stays `"true"` until then
- Connection suggestions (phase 4) and the dashboard (phase 2)
