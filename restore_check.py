# screenpipe — AI that knows everything you've seen, said, or heard; https://screenpipe.com
import argparse

from backup import restore_check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a local backup by restoring it into a temporary DB")
    parser.add_argument("backup_file", nargs="?", help="Backup path. If omitted, the latest local backup is used.")
    parser.add_argument(
        "--postgres-restore-url",
        default="",
        help="Disposable PostgreSQL URL for real .sql.gz restore drill (or set POSTGRES_RESTORE_TEST_DATABASE_URL)",
    )
    args = parser.parse_args(argv)
    ok = restore_check(args.backup_file, postgres_restore_url=args.postgres_restore_url.strip() or None)
    print("restore-check: ok" if ok else "restore-check: failed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
