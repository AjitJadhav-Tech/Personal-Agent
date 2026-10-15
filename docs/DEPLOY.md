# Deploying Personal Agent

Region: **us-east-1**. Model: **Kimi K3 on Amazon Bedrock** (`us.moonshotai.kimi-k3`). Approval requests, the daily report, alarms and budget alerts go to **ajit.jadhav.ai@outlook.com** (set in `infra/cdk.json`).

Deploys run in **AWS CodeBuild**: every push to the deploy branch runs lint and tests, builds the agent image and deploys. You only do the one-time setup below, all in the AWS console. Nothing needs to be installed on your computer.

The agent starts in **dry-run mode**: it drafts posts, finds jobs, emails you for approval and records your decisions, but never publishes anything. See *LinkedIn publishing* below to go live.

## 1. One-time setup in the AWS console (us-east-1)

1. **Kimi K3 access.** Open Amazon Bedrock → *Playgrounds* → *Chat*, pick **Kimi K3** (Moonshot AI), and send one test message. If Bedrock asks you to accept the model's terms, accept them. This confirms your account can call the model.
2. **Agent traces.** In CloudWatch, turn on *Transaction Search* once (CloudWatch → Application Signals → Transaction search). Agent traces then show in the *GenAI Observability* dashboard.
3. **Connect GitHub.** Open *Developer Tools → Settings → Connections → Create connection*, choose **GitHub**, name it `personal-agent-github`, and install the AWS Connector app on the `AjitJadhav-Tech` account with access to the `Personal-Agent` repository. When the connection shows **Available**, copy its ARN.
4. **Create the pipeline.** Download [`infra/pipeline.template.json`](../infra/pipeline.template.json) from GitHub. Open *CloudFormation → Create stack → With new resources*, choose *Upload a template file*, pick the file, and name the stack `PersonalAgentPipeline`. Paste the connection ARN into **ConnectionArn**. Leave the other parameters as they are unless you renamed the repository or want to deploy from another branch. Tick the IAM acknowledgement and create the stack.

The pipeline starts by itself. Watch it in *CodePipeline → personal-agent*. The first run takes about 15 to 25 minutes, because it also runs `cdk bootstrap` and builds the container. Every later push to the deploy branch redeploys automatically, and a push that fails lint or tests is not deployed.

## 2. Confirm two emails

Check the Outlook inbox (and Junk) for:

1. **Amazon SES verification**: click the link. Until you do, the agent cannot email you.
2. **AWS Notifications subscription confirmation**: click *Confirm subscription* so failure alarms reach you.

SES starts in sandbox mode, which only allows sending to verified addresses. That is fine here because the agent only ever emails you.

**Deliverability note:** the emails are sent *from* your Outlook address through SES, so Outlook may file them in Junk. Mark the first one *Not junk* and add the address to Safe senders. For the most reliable delivery later, verify a domain you own in SES and set `"senderEmail": "agent@yourdomain.com"` in `infra/cdk.json`.

## 3. Try it

In the Lambda console, open **personal-agent-runner**, choose *Test*, and run it with this event:

```json
{"task": "content"}
```

Within a minute or two you should get an email with a post draft. Open it, click **Review and decide**, then approve, edit or reject. Use `{"task": "job_hunt"}` to try the job search (fill in your preferences first) and `{"task": "daily_report"}` for the report.

The approval page only records a decision when you press a button, so Outlook's link scanning can't approve anything. Each link works once and expires (posts after 48 hours, job batches after 7 days).

## 4. Change settings

Edit `profile/preferences.yaml` (target titles, skills, locations, Greenhouse boards, your positioning sentence) or `infra/cdk.json`, and push. The pipeline tests and redeploys.

| Setting (`infra/cdk.json`) | What it does |
|---|---|
| `modelId` | Bedrock model or inference profile (default `us.moonshotai.kimi-k3`) |
| `dryRun` | `"true"` keeps every post unpublished; set `"false"` to publish approved posts |
| `linkedinClientId` | Client ID of your LinkedIn developer app |
| `timezone` | Schedules' timezone (default `Asia/Kolkata`) |
| `monthlyBudgetUsd` | Budget alert threshold (default 50) |

## 5. Schedules (Asia/Kolkata)

| Workflow | When |
|---|---|
| Job search | 08:00, 13:00, 18:00 daily |
| Post draft for approval | Tuesday and Thursday 07:30 |
| Daily report | 20:00 daily |

## 6. Operating it

- **Pause everything:** in *Systems Manager → Parameter Store*, set `/personal-agent/paused` to `true` (back to `false` to resume). If the flag can't be read, the agent treats itself as paused.
- **Logs and traces:** CloudWatch → GenAI Observability → Bedrock AgentCore; deploy logs in CodeBuild → `personal-agent-deploy`.
- **Cost guard:** an AWS Budget emails you at 80% of `monthlyBudgetUsd`, actual or forecast.
- **Security:** the CodeBuild role has administrator rights so it can bootstrap and deploy. Anyone who can push to the deploy branch can change this AWS account, so keep the repository private and the branch protected.
- **Remove:** delete the `PersonalAgent` and `PersonalAgentPipeline` stacks in CloudFormation. The DynamoDB table is kept on purpose; delete it manually if you want the history gone.

## Deploying from your own computer (optional)

With Python 3.12+, Node.js 22 and Docker (ARM64 builds) installed:

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
npm install -g aws-cdk
cd infra && cdk bootstrap aws://<ACCOUNT_ID>/us-east-1 && cdk deploy PersonalAgent
```

If you change `infra/stacks/pipeline_stack.py`, regenerate the console template with `python infra/export_pipeline_template.py` and update the `PersonalAgentPipeline` stack with it.

## Apply packs (phase 1)

When you approve jobs in a match email, the agent writes a tailored resume and cover letter for each one from `profile/master_profile.yaml` and emails them to you as PDFs, with the job link, the match reason and any gaps. Every resume bullet and cover-letter paragraph must cite facts from your profile; numbers must match those facts exactly; and a second model pass reviews each line. Anything that fails is rewritten once, then replaced with your profile's own wording (or dropped from the cover letter). The email says how many lines were adjusted. You still read and submit every application yourself.

To make the documents stronger, add true facts and metrics to `profile/master_profile.yaml` and push.

## LinkedIn publishing (phase 3)

Approved posts go out through LinkedIn's official Posts API, on your account, and only after you approve them.

1. **Developer app** (linkedin.com/developers): products *Share on LinkedIn* and *Sign In with LinkedIn using OpenID Connect*; under *Auth*, redirect URL `<ApprovalApiUrl>/oauth/linkedin/callback`. Put its Client ID in `infra/cdk.json` as `linkedinClientId`.
2. **Client secret:** Secrets Manager → *Store a new secret* → *Other type of secret* → *Plaintext*, paste only the secret, name it `personal-agent/linkedin-client-secret`.
3. **Connect:** run the Lambda test `{"task":"daily_report"}`; the email has a *Connect LinkedIn* button. Sign in and allow. The page should say "LinkedIn connected as …".
4. **Go live:** set `"dryRun": "false"` in `infra/cdk.json` and push.

LinkedIn tokens last about 60 days and self-serve apps get no refresh token, so the daily report shows the connection status and brings the *Connect LinkedIn* button back in the last week. If the connection lapses, an approved post is not published; the daily report says so and you can paste it on LinkedIn yourself. The token is kept in the `LinkedInToken` secret (we use our own sign-in page rather than AgentCore Identity, which keeps the setup to the steps above).

## Not built yet

- Connection suggestions (phase 4) and the dashboard (phase 2)
