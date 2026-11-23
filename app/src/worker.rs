use crate::model::{WorkerEnvelope, WorkerEvent};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;

pub struct WorkerJob {
    pub events: Receiver<WorkerEvent>,
    child: Child,
}

impl WorkerJob {
    pub fn cancel(&mut self) {
        let _ = self.child.kill();
    }

    pub fn try_finished(&mut self) -> std::io::Result<Option<std::process::ExitStatus>> {
        self.child.try_wait()
    }
}

pub fn discover_worker() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("FOSSPULSER_WORKER") {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Some(path);
        }
    }
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let candidates = [
        root.join(".pixi/envs/default/bin/python"),
        root.join(".pixi/envs/default/python.exe"),
    ];
    candidates.into_iter().find(|path| path.is_file())
}

pub fn start(envelope: WorkerEnvelope, worker: &Path) -> std::io::Result<WorkerJob> {
    let mut child = Command::new(worker)
        .arg("-m")
        .arg("pipeline.worker")
        .current_dir(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let payload = serde_json::to_vec(&envelope).map_err(std::io::Error::other)?;
    child
        .stdin
        .take()
        .ok_or_else(|| std::io::Error::other("worker stdin unavailable"))?
        .write_all(&payload)?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| std::io::Error::other("worker stdout unavailable"))?;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Ok(event) = serde_json::from_str::<WorkerEvent>(&line) {
                if sender.send(event).is_err() {
                    break;
                }
            }
        }
    });
    Ok(WorkerJob {
        events: receiver,
        child,
    })
}
