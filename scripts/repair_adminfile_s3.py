#!/usr/bin/env python3
"""Upload a local PDF to the S3 key for an existing adminfiles row (or create one).

Use when database metadata exists but the S3 object is missing.

Example:
  source env/app.env
  python scripts/repair_adminfile_s3.py \\
    --patient-id 10314 \\
    --local-path /path/to/Case_ShHa_1969_.docx__1_.pdf \\
    --name Case_ShHa_1969_.docx__1_.pdf \\
    --category "Level 4 - Full Data Assessment (With Oral Appliance Prescription)"
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import boto3
import pymysql
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / "env" / "app.env")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair missing S3 object for admin report")
    parser.add_argument("--patient-id", type=int, required=True)
    parser.add_argument("--local-path", type=Path, required=True)
    parser.add_argument("--name", required=True, help="Filename as stored in adminfiles.name")
    parser.add_argument("--category", default=None, help="adminfiles.file_category")
    parser.add_argument("--replace-id", type=int, default=None, help="Update existing adminfiles.id instead of insert")
    args = parser.parse_args()

    local_path = args.local_path.expanduser().resolve()
    if not local_path.is_file():
        raise SystemExit(f"File not found: {local_path}")

    bucket = os.environ["S3_BUCKET_NAME"]
    s3_key = f"patients/{args.patient_id}/reports/admin-files/{args.name}"
    file_size = local_path.stat().st_size

    s3 = boto3.client(
        "s3",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    with open(local_path, "rb") as fh:
        s3.upload_fileobj(fh, bucket, s3_key, ExtraArgs={"ContentType": "application/pdf"})
    s3.head_object(Bucket=bucket, Key=s3_key)
    print(f"Uploaded to s3://{bucket}/{s3_key} ({file_size} bytes)")

    conn = pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        user=os.environ["DB_USERNAME"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset="utf8mb4",
    )
    cur = conn.cursor()
    if args.replace_id:
        cur.execute(
            "UPDATE adminfiles SET s3_key=%s, file_size=%s, upload_date=%s WHERE id=%s AND patient_id=%s",
            (s3_key, file_size, datetime.utcnow(), args.replace_id, args.patient_id),
        )
        if cur.rowcount != 1:
            raise SystemExit(f"adminfiles id {args.replace_id} not updated")
        admin_id = args.replace_id
    else:
        cur.execute(
            """
            INSERT INTO adminfiles
              (name, patient_id, file_type, file_size, s3_key, upload_date, is_public, file_category)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                args.name,
                args.patient_id,
                "application/pdf",
                file_size,
                s3_key,
                datetime.utcnow(),
                1,
                args.category,
            ),
        )
        admin_id = cur.lastrowid
    conn.commit()
    conn.close()
    print(f"adminfiles row id={admin_id}")


if __name__ == "__main__":
    main()
