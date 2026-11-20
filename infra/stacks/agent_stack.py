"""One copy of the whole system. Deployed twice: staging and prod.

Serverless on purpose: no VPC, NAT gateway, OpenSearch or Aurora, so a week
of running costs a few dollars. README.md explains the production equivalents.
"""
from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_authorizers as authorizers,
    aws_apigatewayv2_integrations as integrations,
    aws_bedrock as bedrock,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cw,
    aws_codedeploy as codedeploy,
    aws_cognito as cognito,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_s3vectors as s3vectors,
)
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"


class AgentStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, model_id: str,
                 canary: bool, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        region, account = self.region, self.account
        destroy = RemovalPolicy.DESTROY  # demo project: everything is removed on `cdk destroy`

        # ---------- RAG: policy documents -> S3 Vectors knowledge base ----------
        docs_bucket = s3.Bucket(
            self, "PolicyDocs", removal_policy=destroy, auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
        )
        s3deploy.BucketDeployment(
            self, "UploadPolicies", sources=[s3deploy.Source.asset(str(ROOT / "policies"))],
            destination_bucket=docs_bucket,
        )

        vector_bucket = s3vectors.CfnVectorBucket(self, "VectorBucket")
        vector_index = s3vectors.CfnIndex(
            self, "VectorIndex",
            vector_bucket_arn=vector_bucket.attr_vector_bucket_arn,
            index_name="policies",
            data_type="float32",
            dimension=1024,
            distance_metric="cosine",
            # Bedrock stores chunk text as metadata; keep it non-filterable (size limits).
            metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
            ),
        )

        kb_role = iam.Role(
            self, "KnowledgeBaseRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com", conditions={
                "StringEquals": {"aws:SourceAccount": account}}),
        )
        kb_policy = iam.Policy(self, "KnowledgeBasePolicy", roles=[kb_role], statements=[
            iam.PolicyStatement(actions=["bedrock:InvokeModel"],
                                resources=[f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL}"]),
            iam.PolicyStatement(actions=["s3:GetObject", "s3:ListBucket"],
                                resources=[docs_bucket.bucket_arn, docs_bucket.arn_for_objects("*")]),
            iam.PolicyStatement(
                actions=["s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors",
                         "s3vectors:QueryVectors", "s3vectors:GetIndex", "s3vectors:ListVectors"],
                resources=[vector_index.attr_index_arn]),
        ])

        knowledge_base = bedrock.CfnKnowledgeBase(
            self, "KnowledgeBase",
            name=f"drive-policies-{stage}",
            role_arn=kb_role.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL}",
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    index_arn=vector_index.attr_index_arn),
            ),
        )
        knowledge_base.node.add_dependency(kb_policy)

        data_source = bedrock.CfnDataSource(
            self, "PolicyDataSource",
            knowledge_base_id=knowledge_base.attr_knowledge_base_id,
            name="policy-documents",
            data_deletion_policy="RETAIN",  # lets the stack delete cleanly
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=docs_bucket.bucket_arn),
            ),
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(
                    chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=300, overlap_percentage=10),
                ),
            ),
        )

        # ---------- Guardrail: block prompt attacks, mask personal data ----------
        guardrail = bedrock.CfnGuardrail(
            self, "Guardrail",
            name=f"drive-agent-{stage}",
            blocked_input_messaging="This request was blocked by the safety filter.",
            blocked_outputs_messaging="This response was blocked by the safety filter.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(filters_config=[
                bedrock.CfnGuardrail.ContentFilterConfigProperty(
                    type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"),
            ]),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(type=t, action="ANONYMIZE")
                    for t in ["EMAIL", "PHONE", "NAME", "ADDRESS", "CREDIT_DEBIT_CARD_NUMBER"]
                ]),
        )
        guardrail_version = bedrock.CfnGuardrailVersion(
            self, "GuardrailVersion", guardrail_identifier=guardrail.attr_guardrail_id,
            description="Pinned version used by the API",
        )

        # ---------- Data: cases + insert-only audit log ----------
        cases = ddb.TableV2(
            self, "Cases", partition_key=ddb.Attribute(name="caseId", type=ddb.AttributeType.STRING),
            removal_policy=destroy, point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True),
            global_secondary_indexes=[ddb.GlobalSecondaryIndexPropsV2(
                index_name="status-index",
                partition_key=ddb.Attribute(name="status", type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name="createdAt", type=ddb.AttributeType.STRING))],
        )
        audit = ddb.TableV2(
            self, "AuditLog",
            partition_key=ddb.Attribute(name="caseId", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="eventKey", type=ddb.AttributeType.STRING),
            removal_policy=destroy, point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True),
        )

        # ---------- Sign-in: Cognito with reps and reviewers groups ----------
        user_pool = cognito.UserPool(
            self, "Users", self_sign_up_enabled=False, sign_in_aliases=cognito.SignInAliases(email=True),
            password_policy=cognito.PasswordPolicy(min_length=10), removal_policy=destroy,
        )
        for group in ("reps", "reviewers"):
            cognito.CfnUserPoolGroup(self, f"Group-{group}", user_pool_id=user_pool.user_pool_id, group_name=group)
        web_client = user_pool.add_client(
            "WebClient", auth_flows=cognito.AuthFlow(user_password=True), generate_secret=False,
            id_token_validity=Duration.hours(1),
        )

        # ---------- The API function (agent + routes) ----------
        log_group = logs.LogGroup(self, "ApiLogs", retention=logs.RetentionDays.TWO_WEEKS, removal_policy=destroy)
        fn = lambda_.Function(
            self, "ApiFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.X86_64,
            handler="app.handler.handler",
            code=lambda_.Code.from_asset(str(ROOT / "build" / "lambda")),
            memory_size=1024,
            timeout=Duration.seconds(29),
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
            environment={
                "STAGE": stage,
                "MODEL_ID": model_id,
                "KNOWLEDGE_BASE_ID": knowledge_base.attr_knowledge_base_id,
                "GUARDRAIL_ID": guardrail.attr_guardrail_id,
                "GUARDRAIL_VERSION": guardrail_version.attr_version,
                "CASES_TABLE": cases.table_name,
                "AUDIT_TABLE": audit.table_name,
            },
        )
        cases.grant_read_write_data(fn)
        fn.add_to_role_policy(iam.PolicyStatement(actions=["dynamodb:PutItem"], resources=[audit.table_arn]))
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                f"arn:aws:bedrock:{region}:{account}:inference-profile/*",
                "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
            ]))
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:Retrieve"], resources=[knowledge_base.attr_knowledge_base_arn]))
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:ApplyGuardrail"],
            resources=[guardrail.attr_guardrail_arn, f"{guardrail.attr_guardrail_arn}:*"]))

        # ---------- Safe releases: alias + CodeDeploy canary + rollback alarm ----------
        live = lambda_.Alias(self, "LiveAlias", alias_name="live", version=fn.current_version)
        errors_alarm = cw.Alarm(
            self, "LiveErrors", metric=live.metric_errors(period=Duration.minutes(1), statistic="Sum"),
            threshold=1, evaluation_periods=1, treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            alarm_description="Any errors on the live alias roll a canary back",
        )
        codedeploy.LambdaDeploymentGroup(
            self, "Deployments", alias=live, alarms=[errors_alarm],
            deployment_config=(codedeploy.LambdaDeploymentConfig.CANARY_10_PERCENT_5_MINUTES if canary
                               else codedeploy.LambdaDeploymentConfig.ALL_AT_ONCE),
        )

        # ---------- HTTP API with Cognito login and throttling ----------
        api = apigw.HttpApi(
            self, "Api", create_default_stage=False,
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"], allow_headers=["authorization", "content-type"],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.OPTIONS]),
        )
        api_stage = apigw.HttpStage(
            self, "ApiStage", http_api=api, stage_name="$default", auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10),  # caps cost if the URL leaks
        )
        integration = integrations.HttpLambdaIntegration("LiveIntegration", live)
        auth = authorizers.HttpUserPoolAuthorizer("CognitoAuth", user_pool, user_pool_clients=[web_client])
        api.add_routes(path="/health", methods=[apigw.HttpMethod.GET], integration=integration)
        api.add_routes(path="/cases", methods=[apigw.HttpMethod.POST, apigw.HttpMethod.GET],
                       integration=integration, authorizer=auth)
        api.add_routes(path="/cases/{caseId}", methods=[apigw.HttpMethod.GET],
                       integration=integration, authorizer=auth)
        api.add_routes(path="/cases/{caseId}/decision", methods=[apigw.HttpMethod.POST],
                       integration=integration, authorizer=auth)

        # ---------- Web page on CloudFront ----------
        site_bucket = s3.Bucket(self, "Site", removal_policy=destroy, auto_delete_objects=True,
                                block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True)
        distribution = cloudfront.Distribution(
            self, "SiteCdn", default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS),
        )
        s3deploy.BucketDeployment(
            self, "UploadSite", destination_bucket=site_bucket, distribution=distribution,
            distribution_paths=["/*"],
            sources=[
                s3deploy.Source.asset(str(ROOT / "web")),
                s3deploy.Source.json_data("config.json", {
                    "stage": stage, "region": region, "apiUrl": api_stage.url,
                    "userPoolClientId": web_client.user_pool_client_id}),
            ],
        )

        # ---------- Dashboard ----------
        dashboard = cw.Dashboard(self, "Dashboard", dashboard_name=f"drive-agent-{stage}")
        dashboard.add_widgets(
            cw.GraphWidget(title="Requests and errors (live alias)",
                           left=[live.metric_invocations(period=Duration.minutes(5)),
                                 live.metric_errors(period=Duration.minutes(5))]),
            cw.GraphWidget(title="Latency p95 (ms)",
                           left=[live.metric_duration(period=Duration.minutes(5), statistic="p95")]),
        )

        for name, value in {
            "ApiUrl": api_stage.url,
            "WebsiteUrl": f"https://{distribution.distribution_domain_name}",
            "UserPoolId": user_pool.user_pool_id,
            "UserPoolClientId": web_client.user_pool_client_id,
            "KnowledgeBaseId": knowledge_base.attr_knowledge_base_id,
            "DataSourceId": data_source.attr_data_source_id,
            "GuardrailId": guardrail.attr_guardrail_id,
            "GuardrailVersion": guardrail_version.attr_version,
            "ModelId": model_id,
            "CasesTable": cases.table_name,
        }.items():
            CfnOutput(self, f"Out{name}", key=name, value=value)
