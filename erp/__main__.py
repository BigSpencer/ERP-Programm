"""Start: ``python -m erp`` (Optionen: --setup, --init-db, --demo-data)."""
import sys


def run() -> int:
    args = sys.argv[1:]
    if "--init-db" in args or "--demo-data" in args:
        # Kommandozeilen-Werkzeuge ohne Oberfläche (z. B. für Server-Setup)
        from . import config, db
        cfg, _ = config.load()
        if cfg.backend == "sqlite":
            config.config_dir().mkdir(parents=True, exist_ok=True)
        db.connect(cfg.url())
        db.init_schema()
        print(f"Schema bereit: {cfg.display()}")
        if "--demo-data" in args:
            from .demo import load_demo_data
            with db.session_scope() as s:
                load_demo_data(s)
            print("Demodaten geladen.")
        return 0
    from .ui.app import main
    return main(sys.argv)


if __name__ == "__main__":
    sys.exit(run())
