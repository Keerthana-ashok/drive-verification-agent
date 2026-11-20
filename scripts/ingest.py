"""Sync the policy documents into the knowledge base and wait until it's done.

Run after every deploy (new or changed policies only become searchable after this).
Usage: python scripts/ingest.py DriveAgent-Staging
"""
import sys
import time

import boto3

from stack_outputs import get_outputs


def main() -> None:
    stack = sys.argv[1]
    out = get_outputs(stack)
    client = boto3.client("bedrock-agent", region_name="us-east-1")
    job = client.start_ingestion_job(knowledgeBaseId=out["KnowledgeBaseId"], dataSourceId=out["DataSourceId"])
    job_id = job["ingestionJob"]["ingestionJobId"]
    print(f"Started ingestion job {job_id} for {stack}")
    for _ in range(60):
        info = client.get_ingestion_job(knowledgeBaseId=out["KnowledgeBaseId"], dataSourceId=out["DataSourceId"],
                                        ingestionJobId=job_id)["ingestionJob"]
        status = info["status"]
        if status == "COMPLETE":
            print(f"Done: {info.get('statistics', {})}")
            return
        if status == "FAILED":
            sys.exit(f"Ingestion failed: {info.get('failureReasons')}")
        time.sleep(5)
    sys.exit("Ingestion did not finish within 5 minutes")


if __name__ == "__main__":
    main()
