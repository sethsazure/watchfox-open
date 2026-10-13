"""`watchfox serve` runs the cloud app: the API, the website and every saved watch."""
import argparse

from . import __version__, config

FOX = r"""
 /\___/\
 \(o o)/   WatchFox {version}
  \ v /    Tell it which mailbox to watch and what to catch.
   \_/
"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="watchfox", description="WatchFox server.")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the API, the website and all watches (default)")
    s.add_argument("--host", default=config.HOST)
    s.add_argument("--port", type=int, default=config.PORT)
    sp = sub.add_parser("set-plan", help="switch an organization to a plan after it has paid: watchfox set-plan EMAIL kit|tracker|alpha [--annual]")
    sp.add_argument("email")
    sp.add_argument("plan")
    sp.add_argument("--annual", action="store_true")
    sub.add_parser("helcim-setup", help="create the four WatchFox payment plans in Helcim (Tracker and Alpha, monthly and yearly) if they do not exist")
    cp = sub.add_parser("copy-db", help="copy a SQLite database into PostgreSQL: watchfox copy-db --from FILE --to postgresql://...")
    cp.add_argument("--from", dest="src", required=True)
    cp.add_argument("--to", dest="dst", required=True)
    args = p.parse_args(argv)
    if args.cmd == "helcim-setup":
        from . import billing, helcim, payments

        if not helcim.configured():
            raise SystemExit("Set HELCIM_API_TOKEN first.")
        term = helcim.terminal_id()
        if not term:
            raise SystemExit(f"No active {helcim.CURRENCY} card terminal found in Helcim.")
        have = {p.get("name"): p for p in helcim.plans()}
        for plan in ("tracker", "alpha"):
            for period, helcim_period, key in (("monthly", "monthly", "monthly"), ("annual", "yearly", "annual")):
                name = payments.plan_name(plan, helcim_period)
                if name in have:
                    print(f"exists   {name} (id {have[name]['id']})")
                    continue
                made = helcim.create_plan(name, float(billing.PLANS[plan][key]), helcim_period, term)
                print(f"created  {name} (id {made.get('id')}) {billing.PLANS[plan][key]} {helcim.CURRENCY} {helcim_period}")
        return
    if args.cmd == "copy-db":
        from .dbcopy import copy

        for table, n in copy(args.src, args.dst).items():
            print(f"{table:15s} {n} rows")
        return
    if args.cmd == "set-plan":
        from . import billing, store

        if args.plan not in billing.PLANS:
            raise SystemExit(f"Unknown plan. Choose one of: {', '.join(billing.PLANS)}")
        u = store.user_by_email(args.email)
        if not u:
            raise SystemExit("No account with that email.")
        wid = (store.member(u["id"]) or {"workspace_id": u["id"]})["workspace_id"]
        store.set_plan(wid, args.plan, "annual" if args.annual else "monthly")
        print(f"{args.email} (organization {wid}) is now on {billing.PLANS[args.plan]['name']}, billed {'annually' if args.annual else 'monthly'}.")
        return
    host, port = getattr(args, "host", config.HOST), getattr(args, "port", config.PORT)
    import uvicorn

    print(FOX.format(version=__version__))
    print(f"Serving on http://{host}:{port}  ·  data in {config.DB_PATH.parent}\n", flush=True)
    from .server import app

    uvicorn.run(app, host=host, port=port, log_level="warning", proxy_headers=True, server_header=False)


if __name__ == "__main__":
    main()
