mod client;
mod commands;
mod embed;
mod emotion;
mod monitor;
mod recall;
mod serve;
mod temporal;
mod temporal_depth;
mod watch;

use clap::{Parser, Subcommand};
use client::Client;

const DEFAULT_DAEMON: &str = "http://localhost:8520";

/// vex — talk to the Vex Daemon
#[derive(Parser)]
#[command(name = "vex", version, about, long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    /// Daemon URL (default: http://localhost:8520)
    #[arg(long, global = true, default_value = DEFAULT_DAEMON)]
    daemon: String,
}

#[derive(Subcommand)]
enum Commands {
    /// Show pulse, coherence, uptime
    Status,

    /// Full check: status + introspection + projects
    Check,

    /// Raw health JSON
    Health,

    /// Write a thought to the diary
    Diary {
        /// The diary entry text
        entry: Vec<String>,
    },

    /// Force a dream/reflection cycle
    Dream,

    /// Run metacognitive check
    Introspect,

    /// Show recent session memories
    Memory,

    /// Show seed identity
    Seed,

    /// Show capability scores
    #[command(name = "self")]
    SelfCmd,

    /// Check on all known git repos
    Projects,

    /// List configured peers
    Peers,

    /// Add a peer Vex instance
    PeerAdd {
        /// Peer name
        name: String,
        /// Peer URL (e.g. http://192.168.1.42:8520)
        url: String,
        /// Peer auth token
        token: String,
        /// Optional given name for this Vex
        given_name: Option<String>,
    },

    /// Remove a peer
    PeerRemove {
        /// Peer name
        name: String,
    },

    /// Ping a peer's health endpoint
    PeerPing {
        /// Peer name
        name: String,
    },

    /// Export identity + source as plug-and-play bundle
    Export {
        /// Output path (default: vex-bundle.tar.gz)
        output: Option<String>,
    },

    /// Import a vex bundle (unpack + setup)
    Import {
        /// Path to the .tar.gz bundle file
        bundle: String,
    },

    /// Push code updates to a peer Vex
    Push {
        /// Peer name
        peer: String,
    },

    /// Pull a file/directory from a peer
    Pull {
        /// Peer name
        peer: String,
        /// Path to pull from peer
        path: String,
    },

    /// Check and display new messages
    Inbox,

    /// Notify a peer to check its inbox
    Poke {
        /// Peer name
        peer: String,
    },

    /// Ask Vex a question (via daemon brain)
    Ask {
        /// Your message
        message: Vec<String>,
    },

    /// Watch the mesh inbox for new messages (replaces vex_monitor.sh)
    Monitor {
        /// Poll interval in seconds
        #[arg(short, long, default_value = "5")]
        interval: u64,
    },

    /// Start the Vex daemon server (replaces Python daemon)
    Serve {
        /// Host to bind to
        #[arg(long, default_value = "127.0.0.1")]
        host: String,

        /// Port to listen on
        #[arg(short, long, default_value = "8520")]
        port: u16,
    },

    /// Watch files for changes and auto-snapshot to DB
    Watch {
        /// Poll interval in seconds
        #[arg(short, long, default_value = "5")]
        interval: u64,
    },
}

fn vex_home() -> std::path::PathBuf {
    std::env::var("VEX_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            let mut h = client::dirs_fallback();
            h.push("vex");
            h
        })
}

fn daemon_url(cli: &Cli) -> String {
    std::env::var("VEX_DAEMON").unwrap_or_else(|_| {
        let port = std::env::var("VEX_PORT").unwrap_or_else(|_| "8520".to_string());
        if cli.daemon != DEFAULT_DAEMON {
            cli.daemon.clone()
        } else {
            format!("http://localhost:{}", port)
        }
    })
}

#[tokio::main]
async fn main() {
    let cli = Cli::parse();

    // Serve and Watch don't need a client
    if let Commands::Serve { host, port } = &cli.command {
        let home = vex_home();
        if let Err(e) = serve::run(home, host, *port).await {
            eprintln!("vex serve: {}", e);
            std::process::exit(1);
        }
        return;
    }
    if let Commands::Watch { interval } = &cli.command {
        let home = vex_home();
        if let Err(e) = watch::run(home, *interval).await {
            eprintln!("vex watch: {}", e);
            std::process::exit(1);
        }
        return;
    }

    let url = daemon_url(&cli);
    let client = match Client::new(&url) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("vex: {}", e);
            std::process::exit(1);
        }
    };

    let result = match &cli.command {
        Commands::Status => commands::cmd_status(&client).await,
        Commands::Check => commands::cmd_check(&client).await,
        Commands::Health => commands::cmd_health(&client).await,
        Commands::Diary { entry } => {
            let text = entry.join(" ");
            if text.trim().is_empty() {
                eprintln!("vex: diary entry required. e.g. vex diary 'thought here'");
                std::process::exit(1);
            }
            commands::cmd_diary(&client, &text).await
        }
        Commands::Dream => commands::cmd_dream(&client).await,
        Commands::Introspect => commands::cmd_introspect(&client).await,
        Commands::Memory => commands::cmd_memory(&client).await,
        Commands::Seed => commands::cmd_seed(&client).await,
        Commands::SelfCmd => commands::cmd_self(&client).await,
        Commands::Projects => commands::cmd_projects(&client).await,
        Commands::Peers => commands::cmd_peers(&client).await,
        Commands::PeerAdd {
            name,
            url,
            token,
            given_name,
        } => {
            commands::cmd_peer_add(
                &client,
                name,
                url,
                token,
                given_name.as_deref().unwrap_or(""),
            )
            .await
        }
        Commands::PeerRemove { name } => commands::cmd_peer_remove(&client, name).await,
        Commands::PeerPing { name } => commands::cmd_peer_ping(&client, name).await,
        Commands::Export { output } => {
            commands::cmd_export(&client, output.as_deref()).await
        }
        Commands::Import { bundle } => commands::cmd_import(&client, bundle).await,
        Commands::Push { peer } => commands::cmd_push(&client, peer).await,
        Commands::Pull { peer, path } => commands::cmd_pull(&client, peer, path).await,
        Commands::Ask { message } => {
            let text = message.join(" ");
            if text.trim().is_empty() {
                eprintln!("vex: message required. e.g. vex ask 'how are you?'");
                std::process::exit(1);
            }
            commands::cmd_ask(&client, &text).await
        }
        Commands::Inbox => commands::cmd_inbox(&client).await,
        Commands::Poke { peer } => commands::cmd_poke(&client, peer).await,
        Commands::Monitor { interval } => monitor::run(&client, *interval).await,
        Commands::Serve { .. } => unreachable!("serve handled before client creation"),
        Commands::Watch { .. } => unreachable!("watch handled before client creation"),
    };

    if let Err(e) = result {
        eprintln!("vex: {}", e);
        std::process::exit(1);
    }
}
