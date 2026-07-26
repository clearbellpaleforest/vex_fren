pub mod diary;
pub mod inbox;
pub mod memory;
pub mod peers;
pub mod projects;
pub mod status;
pub mod transfer;

// Re-export all command functions
pub use diary::{cmd_diary, cmd_dream, cmd_introspect};
pub use inbox::{cmd_ask, cmd_inbox, cmd_poke};
pub use memory::{cmd_memory, cmd_seed, cmd_self};
pub use peers::{cmd_peer_add, cmd_peer_ping, cmd_peer_remove, cmd_peers};
pub use projects::cmd_projects;
pub use status::{cmd_check, cmd_health, cmd_status};
pub use transfer::{cmd_export, cmd_import, cmd_pull, cmd_push};
