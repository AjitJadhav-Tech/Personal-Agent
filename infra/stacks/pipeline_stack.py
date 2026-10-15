"""CodePipeline + CodeBuild: every push to the deploy branch runs lint and tests, then deploys
the PersonalAgent stack. CodeBuild builds the ARM64 agent image natively, so nothing needs to be
installed on your computer.

The stack takes CloudFormation parameters (not CDK context) so its synthesized template can be
uploaded once in the CloudFormation console; see docs/DEPLOY.md.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_codebuild as codebuild,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as actions,
    aws_iam as iam,
    aws_logs as logs,
)
from constructs import Construct

BUILDSPEC = {
    "version": "0.2",
    "phases": {
        "install": {
            "runtime-versions": {"python": "3.13", "nodejs": "22"},
            "commands": [
                "python -m venv .venv",
                ". .venv/bin/activate && pip install -q --upgrade pip && pip install -q -e '.[dev]'",
                "npm install -g aws-cdk@2",
            ],
        },
        "pre_build": {
            "commands": [". .venv/bin/activate && ruff check . && python -m pytest -q"],
        },
        "build": {
            "commands": [
                ". .venv/bin/activate && cd infra"
                " && ACCOUNT=$(aws sts get-caller-identity --query Account --output text)"
                " && cdk bootstrap aws://$ACCOUNT/us-east-1"
                " && cdk deploy PersonalAgent --require-approval never"
                " --outputs-file ../outputs.json",
            ],
        },
        "post_build": {"commands": ["cat outputs.json || true"]},
    },
}


class PipelineStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        connection_arn = cdk.CfnParameter(
            self,
            "ConnectionArn",
            description="ARN of the GitHub connection (Developer Tools > Settings > Connections)",
        )
        owner = cdk.CfnParameter(self, "RepoOwner", default="AjitJadhav-Tech")
        repo = cdk.CfnParameter(self, "RepoName", default="Personal-Agent")
        branch = cdk.CfnParameter(
            self,
            "Branch",
            default="claude/project-thread-8bpw44",
            description="Pushes to this branch are tested and deployed",
        )

        project = codebuild.PipelineProject(
            self,
            "Deploy",
            project_name="personal-agent-deploy",
            description="Lint, test and cdk deploy the personal agent",
            build_spec=codebuild.BuildSpec.from_object(BUILDSPEC),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxArmBuildImage.AMAZON_LINUX_2023_STANDARD_3_0,
                compute_type=codebuild.ComputeType.SMALL,
                privileged=True,  # Docker, for the agent container image
            ),
            timeout=cdk.Duration.minutes(60),
            logging=codebuild.LoggingOptions(
                cloud_watch=codebuild.CloudWatchLoggingOptions(
                    log_group=logs.LogGroup(
                        self, "DeployLogs", retention=logs.RetentionDays.THREE_MONTHS
                    )
                )
            ),
        )
        # cdk bootstrap (first run) creates IAM roles, buckets and repositories, so the build
        # role needs broad rights. Anyone who can push to the deploy branch can therefore change
        # this AWS account: keep the repository private and the branch protected.
        project.role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
        )

        source_output = codepipeline.Artifact("Source")
        codepipeline.Pipeline(
            self,
            "Pipeline",
            pipeline_name="personal-agent",
            pipeline_type=codepipeline.PipelineType.V2,
            restart_execution_on_update=False,
            cross_account_keys=False,  # single account: no KMS key needed
            stages=[
                codepipeline.StageProps(
                    stage_name="Source",
                    actions=[
                        actions.CodeStarConnectionsSourceAction(
                            action_name="GitHub",
                            connection_arn=connection_arn.value_as_string,
                            owner=owner.value_as_string,
                            repo=repo.value_as_string,
                            branch=branch.value_as_string,
                            output=source_output,
                        ),
                    ],
                ),
                codepipeline.StageProps(
                    stage_name="TestAndDeploy",
                    actions=[
                        actions.CodeBuildAction(
                            action_name="TestAndDeploy", project=project, input=source_output
                        ),
                    ],
                ),
            ],
        )
