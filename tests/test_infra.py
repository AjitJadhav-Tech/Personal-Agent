"""Synthesizes the CDK stack (no AWS access needed) and checks the key resources."""

import sys
from pathlib import Path

import pytest

cdk = pytest.importorskip("aws_cdk")
from aws_cdk.assertions import Match, Template  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from stacks.personal_agent_stack import PersonalAgentStack  # noqa: E402


@pytest.fixture(scope="module")
def template():
    app = cdk.App()
    stack = PersonalAgentStack(
        app,
        "Test",
        notify_email="owner@example.com",
        timezone="Asia/Kolkata",
        dry_run="true",
        monthly_budget_usd=50,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


def test_agentcore_runtime_and_memory(template):
    template.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)
    template.resource_count_is("AWS::BedrockAgentCore::Memory", 1)
    template.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        {
            "EnvironmentVariables": Match.object_like(
                {"DRY_RUN": "true", "NOTIFY_EMAIL": "owner@example.com"}
            ),
        },
    )


def test_email_identity_and_schedules(template):
    template.has_resource_properties(
        "AWS::SES::EmailIdentity", {"EmailIdentity": "owner@example.com"}
    )
    template.resource_count_is("AWS::Scheduler::Schedule", 6)
    template.has_resource_properties(
        "AWS::Scheduler::Schedule", {"ScheduleExpressionTimezone": "Asia/Kolkata"}
    )


def test_approval_api_routes(template):
    template.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "GET /a/{id}"})
    template.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "POST /a/{id}"})


def test_pipeline_deploys_with_codebuild():
    from stacks.pipeline_stack import PipelineStack

    app = cdk.App()
    stack = PipelineStack(
        app, "Pipe", env=cdk.Environment(account="123456789012", region="us-east-1")
    )
    t = Template.from_stack(stack)
    t.has_resource_properties(
        "AWS::CodeBuild::Project",
        {
            "Environment": Match.object_like({"Type": "ARM_CONTAINER", "PrivilegedMode": True}),
        },
    )
    t.has_resource_properties("AWS::CodePipeline::Pipeline", {"PipelineType": "V2"})
    assert "ConnectionArn" in t.to_json()["Parameters"]
