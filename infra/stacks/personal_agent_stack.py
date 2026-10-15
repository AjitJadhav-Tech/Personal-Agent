"""All AWS resources for the personal agent: serverless, us-east-1, approvals by email."""

from __future__ import annotations

import json
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_bedrockagentcore as agentcore,
    aws_budgets as budgets,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as ddb,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_scheduler as scheduler,
    aws_scheduler_targets as targets,
    aws_secretsmanager as secrets,
    aws_ses as ses,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_ssm as ssm,
)
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_FUNCTION_NAME = "personal-agent-runner"
# Created by the owner in the Secrets Manager console (plaintext), never committed.
LINKEDIN_CLIENT_SECRET_NAME = "personal-agent/linkedin-client-secret"

# Schedules in the owner's timezone: (id, task, cron fields).
SCHEDULES = [
    ("JobHuntMorning", "job_hunt", {"hour": "8", "minute": "0"}),
    ("JobHuntAfternoon", "job_hunt", {"hour": "13", "minute": "0"}),
    ("JobHuntEvening", "job_hunt", {"hour": "18", "minute": "0"}),
    # Drafts arrive early so an approved post can go out the same morning.
    ("ContentTuesday", "content", {"hour": "7", "minute": "30", "week_day": "TUE"}),
    ("ContentThursday", "content", {"hour": "7", "minute": "30", "week_day": "THU"}),
    ("DailyReport", "daily_report", {"hour": "20", "minute": "0"}),
]


class PersonalAgentStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        notify_email: str,
        timezone: str,
        dry_run: str,
        monthly_budget_usd: float,
        model_id: str = "us.moonshotai.kimi-k3",
        sender_email: str | None = None,
        linkedin_client_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---------- Data, secrets, switches ----------
        table = ddb.TableV2(
            self,
            "Table",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            time_to_live_attribute="ttl",
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        approval_secret = secrets.Secret(
            self,
            "ApprovalSigningKey",
            description="HMAC key that signs approval links",
            generate_secret_string=secrets.SecretStringGenerator(
                password_length=64, exclude_punctuation=True
            ),
        )
        # Holds the owner's LinkedIn access token once they connect; starts as a placeholder.
        linkedin_token = secrets.Secret(
            self, "LinkedInToken", description="LinkedIn access token for publishing posts"
        )
        linkedin_client_secret = secrets.Secret.from_secret_name_v2(
            self, "LinkedInClientSecret", LINKEDIN_CLIENT_SECRET_NAME
        )
        linkedin_env = {
            "LINKEDIN_CLIENT_ID": linkedin_client_id,
            "LINKEDIN_TOKEN_SECRET_ARN": linkedin_token.secret_arn,
        }
        paused = ssm.StringParameter(
            self,
            "PausedFlag",
            parameter_name="/personal-agent/paused",
            string_value="false",
            description="Kill switch: set to true to stop every outward action",
        )
        # SES sends a verification email to this address on first deploy; click the link once.
        ses.EmailIdentity(self, "NotifyEmailIdentity", identity=ses.Identity.email(notify_email))
        sender_email = sender_email or notify_email
        if sender_email != notify_email:
            ses.EmailIdentity(self, "SenderIdentity", identity=ses.Identity.email(sender_email))

        # ---------- AgentCore Memory ----------
        memory = agentcore.CfnMemory(
            self,
            "Memory",
            name="personal_agent_memory",
            description="Checkpoints for paused runs and long-term owner preferences",
            event_expiry_duration=90,
            memory_strategies=[
                agentcore.CfnMemory.MemoryStrategyProperty(
                    user_preference_memory_strategy=agentcore.CfnMemory.UserPreferenceMemoryStrategyProperty(
                        name="owner_preferences", namespaces=["/preferences/{actorId}"]
                    )
                ),
            ],
        )

        # ---------- Approval page (API Gateway + Lambda) ----------
        code = lambda_.Code.from_asset(
            str(REPO_ROOT / "src"), exclude=["**/__pycache__", "**/*.pyc"]
        )
        approval_fn = lambda_.Function(
            self,
            "ApprovalFn",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="approval_api.handler.handler",
            code=code,
            timeout=cdk.Duration.seconds(15),
            memory_size=256,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "TABLE_NAME": table.table_name,
                "APPROVAL_SECRET_ARN": approval_secret.secret_arn,
                "RUNNER_FUNCTION_NAME": RUNNER_FUNCTION_NAME,
                "NOTIFY_EMAIL": notify_email,
                "LINKEDIN_CLIENT_SECRET_NAME": LINKEDIN_CLIENT_SECRET_NAME,
                **linkedin_env,
            },
        )
        table.grant_read_write_data(approval_fn)
        approval_secret.grant_read(approval_fn)
        linkedin_client_secret.grant_read(approval_fn)
        linkedin_token.grant_read(approval_fn)
        linkedin_token.grant_write(approval_fn)
        approval_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.format_arn(
                        service="lambda",
                        resource="function",
                        resource_name=RUNNER_FUNCTION_NAME,
                        arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME,
                    )
                ],
            )
        )
        api = apigw.HttpApi(self, "ApprovalApi", description="Personal agent approvals")
        api.add_routes(
            path="/a/{id}",
            methods=[apigw.HttpMethod.GET, apigw.HttpMethod.POST],
            integration=integrations.HttpLambdaIntegration("ApprovalIntegration", approval_fn),
        )
        api.add_routes(
            path="/oauth/linkedin/{step}",
            methods=[apigw.HttpMethod.GET],
            integration=integrations.HttpLambdaIntegration("LinkedInIntegration", approval_fn),
        )

        # ---------- AgentCore Runtime ----------
        image = ecr_assets.DockerImageAsset(
            self,
            "AgentImage",
            directory=str(REPO_ROOT),
            file="Dockerfile",
            platform=ecr_assets.Platform.LINUX_ARM64,
        )
        runtime_role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        image.repository.grant_pull(runtime_role)
        table.grant_read_write_data(runtime_role)
        approval_secret.grant_read(runtime_role)
        paused.grant_read(runtime_role)
        linkedin_token.grant_read(runtime_role)
        runtime_role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=[self.format_arn(service="ses", resource="identity", resource_name="*")],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:CreateEvent",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:RetrieveMemoryRecords",
                    "bedrock-agentcore:ListMemoryRecords",
                ],
                resources=[memory.attr_memory_arn],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                # Kimi K3 via the US cross-Region inference profile. Converse is authorized as
                # InvokeModel; the profile routes to the foundation model in any US region.
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    self.format_arn(
                        service="bedrock", resource="inference-profile", resource_name=model_id
                    ),
                    f"arn:{self.partition}:bedrock:*::foundation-model/{model_id.split('.', 1)[1]}",
                ],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                    "cloudwatch:PutMetricData",
                ],
                resources=["*"],
            )
        )

        runtime = agentcore.CfnRuntime(
            self,
            "Runtime",
            agent_runtime_name="personal_agent",
            description="LangGraph personal agent with human-in-the-loop approvals",
            role_arn=runtime_role.role_arn,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=image.image_uri
                )
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC"
            ),
            environment_variables={
                "TABLE_NAME": table.table_name,
                "MEMORY_ID": memory.attr_memory_id,
                "NOTIFY_EMAIL": notify_email,
                "SENDER_EMAIL": sender_email,
                "APPROVAL_BASE_URL": api.api_endpoint,
                "APPROVAL_SECRET_ARN": approval_secret.secret_arn,
                "PAUSED_PARAMETER": paused.parameter_name,
                "DRY_RUN": dry_run,
                "DRAFTING_MODEL_ID": model_id,
                "FAST_MODEL_ID": model_id,
                **linkedin_env,
            },
        )
        runtime.node.add_dependency(runtime_role)

        # ---------- Runner (starts and resumes runs) ----------
        runner_fn = lambda_.Function(
            self,
            "RunnerFn",
            function_name=RUNNER_FUNCTION_NAME,
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="runner.handler.handler",
            code=code,
            timeout=cdk.Duration.minutes(2),
            memory_size=256,
            retry_attempts=0,  # a retry could double-act; failures go to the alarm instead
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={"AGENT_RUNTIME_ARN": runtime.attr_agent_runtime_arn},
        )
        runner_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[runtime.attr_agent_runtime_arn, f"{runtime.attr_agent_runtime_arn}/*"],
            )
        )

        # ---------- Schedules ----------
        for sid, task, cron in SCHEDULES:
            scheduler.Schedule(
                self,
                sid,
                schedule=scheduler.ScheduleExpression.cron(
                    time_zone=cdk.TimeZone.of(timezone), **cron
                ),
                target=targets.LambdaInvoke(
                    runner_fn,
                    input=scheduler.ScheduleTargetInput.from_object({"task": task}),
                    retry_attempts=0,
                ),
                description=f"Start the {task} workflow",
            )

        # ---------- Alarms and budget ----------
        alerts = sns.Topic(self, "Alerts", display_name="Personal agent alerts")
        alerts.add_subscription(subs.EmailSubscription(notify_email))
        for name, fn in (("Runner", runner_fn), ("Approval", approval_fn)):
            alarm = cw.Alarm(
                self,
                f"{name}Errors",
                metric=fn.metric_errors(period=cdk.Duration.minutes(5)),
                threshold=1,
                evaluation_periods=1,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                alarm_description=f"{name} Lambda failed",
            )
            alarm.add_alarm_action(cw_actions.SnsAction(alerts))
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=monthly_budget_usd, unit="USD"),
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type=kind,
                        threshold=80,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[
                        budgets.CfnBudget.SubscriberProperty(
                            address=notify_email, subscription_type="EMAIL"
                        )
                    ],
                )
                for kind in ("ACTUAL", "FORECASTED")
            ],
        )

        cdk.CfnOutput(self, "ApprovalApiUrl", value=api.api_endpoint)
        cdk.CfnOutput(self, "AgentRuntimeArn", value=runtime.attr_agent_runtime_arn)
        cdk.CfnOutput(self, "MemoryId", value=memory.attr_memory_id)
        cdk.CfnOutput(self, "RunnerFunction", value=RUNNER_FUNCTION_NAME)
        cdk.CfnOutput(
            self,
            "TestRunCommand",
            value=json.dumps(
                f"aws lambda invoke --function-name {RUNNER_FUNCTION_NAME} "
                '--payload \'{"task":"content"}\' --cli-binary-format raw-in-base64-out out.json'
            ),
        )
