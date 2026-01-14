import json
import argparse
from database import DatabaseManager


# -----------------------------------
# Command implementations
# -----------------------------------

def cmd_init_db(args):
    db = DatabaseManager(args.db)
    db.init_db()
    print(f"Database initialized at: {args.db}")


def cmd_recent(args):
    db = DatabaseManager(args.db)
    cols, rows = db.get_recent_vessels_data(age=args.age)

    print(f"=== Recent vessel data (last {args.age} minute(s)) ===")
    print(f"Columns: {cols}")
    for row in rows:
        print(dict(row))


def cmd_states(args):
    db = DatabaseManager(args.db)
    states = db.get_vessel_states(args.mmsi)

    print(f"=== All states for MMSI {args.mmsi} ===")
    print(json.dumps(states, indent=2))


def cmd_latest(args):
    db = DatabaseManager(args.db)
    state = db.get_latest_vessel_state(args.mmsi)

    print(f"=== Latest state for MMSI {args.mmsi} ===")
    print(json.dumps(state, indent=2) if state else "No data found.")


def cmd_vessel(args):
    db = DatabaseManager(args.db)
    vessel = db.get_vessel(args.mmsi)

    print(f"=== Vessel record for MMSI {args.mmsi} ===")
    print(json.dumps(vessel, indent=2) if vessel else "No vessel found.")


def cmd_all_vessels(args):
    db = DatabaseManager(args.db)
    vessels = db.get_all_vessels()

    print("=== All Vessels ===")
    print(json.dumps(vessels, indent=2))


# -----------------------------------
# Parser builder
# -----------------------------------

def build_parser():
    parser = argparse.ArgumentParser(description="AIS Database Inspection CLI")

    parser.add_argument(
        "--db",
        default="database/ais_database.db",
        help="Path to SQLite database file"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # init-db
    p_init = sub.add_parser("init-db", help="Initialize database schema")
    p_init.set_defaults(func=cmd_init_db)

    # recent
    p_recent = sub.add_parser("recent", help="Show recently updated vessel data")
    p_recent.add_argument("--age", type=int, default=1, help="Minutes back to fetch")
    p_recent.set_defaults(func=cmd_recent)

    # states
    p_states = sub.add_parser("states", help="Get all vessel states by MMSI")
    p_states.add_argument("--mmsi", required=True)
    p_states.set_defaults(func=cmd_states)

    # latest
    p_latest = sub.add_parser("latest", help="Get latest vessel state by MMSI")
    p_latest.add_argument("--mmsi", required=True)
    p_latest.set_defaults(func=cmd_latest)

    # vessel
    p_vessel = sub.add_parser("vessel", help="Get vessel info by MMSI")
    p_vessel.add_argument("--mmsi", required=True)
    p_vessel.set_defaults(func=cmd_vessel)

    # vessels
    p_vessels = sub.add_parser("vessels", help="List all vessels")
    p_vessels.set_defaults(func=cmd_all_vessels)

    return parser


# -----------------------------------
# Main entry
# -----------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
