# AgentCore Runtime container (ARM64). Serves /invocations and /ping on port 8080.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src DOCKER_CONTAINER=1

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "langgraph>=1.2" "langgraph-checkpoint-aws>=1.2" "bedrock-agentcore>=1.24" \
      "anthropic[bedrock]>=1.14" "boto3>=1.40" "pydantic>=2.8" "pyyaml>=6" \
      "aws-opentelemetry-distro>=0.10"

COPY src ./src
COPY profile ./profile

RUN useradd -m agent
USER agent
EXPOSE 8080

# opentelemetry-instrument sends traces to AgentCore Observability (CloudWatch GenAI dashboard).
CMD ["opentelemetry-instrument", "python", "-m", "agent.app"]
