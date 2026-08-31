from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from .config import get_settings
from .db import Database
from .media_migration import migrate_media_names
from .models import UnknownCluster, VideoIngest
from .pipeline import ProcessingPipeline


def main() -> None:
    parser = argparse.ArgumentParser(prog="doorlockctl")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-models")
    process = sub.add_parser("process")
    process.add_argument("ingest_id")
    sub.add_parser("clusters")
    sub.add_parser("ingest")
    migration = sub.add_parser("migrate-media-names")
    migration.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    database = Database(settings)
    if args.command == "check-models":
        pipeline = ProcessingPipeline(settings, database)
        print(
            json.dumps(
                {"ready": pipeline.ready, "error": pipeline.readiness_error},
                ensure_ascii=False,
            )
        )
    elif args.command == "process":
        pipeline = ProcessingPipeline(settings, database)
        print(pipeline.process(args.ingest_id))
    elif args.command == "migrate-media-names":
        print(
            json.dumps(
                migrate_media_names(database, settings, apply=args.apply),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    elif args.command == "clusters":
        with database.session() as session:
            rows = list(
                session.scalars(
                    select(UnknownCluster).order_by(UnknownCluster.last_seen.desc())
                )
            )
            print(
                json.dumps(
                    [
                        {
                            "id": row.id,
                            "status": row.status,
                            "model_id": row.model_id,
                            "events": row.event_count,
                            "days": row.distinct_days,
                            "first_seen": row.first_seen.isoformat(),
                            "last_seen": row.last_seen.isoformat(),
                        }
                        for row in rows
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
    elif args.command == "ingest":
        with database.session() as session:
            rows = list(
                session.scalars(
                    select(VideoIngest)
                    .order_by(VideoIngest.created_at.desc())
                    .limit(100)
                )
            )
            print(
                json.dumps(
                    [
                        {
                            "id": row.id,
                            "file": row.original_name,
                            "state": row.state,
                            "attempts": row.attempts,
                            "error": row.last_error,
                        }
                        for row in rows
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
