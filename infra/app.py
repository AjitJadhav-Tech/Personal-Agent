#!/usr/bin/env python3
import aws_cdk as cdk
from stacks.personal_agent_stack import PersonalAgentStack

app = cdk.App()
PersonalAgentStack(
    app,
    "PersonalAgent",
    notify_email=app.node.get_context("notifyEmail"),
    timezone=app.node.get_context("timezone"),
    sender_email=app.node.try_get_context("senderEmail") or None,
    dry_run=str(app.node.try_get_context("dryRun") or "true").lower(),
    monthly_budget_usd=float(app.node.try_get_context("monthlyBudgetUsd") or 50),
    env=cdk.Environment(region=app.node.get_context("region")),
)
app.synth()
