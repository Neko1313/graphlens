"""Shared fixtures for graphlens-rust tests."""

from pathlib import Path

import pytest

_LIB_RS = """use std::fmt;
use serde::Serialize;
use crate::util::helper;

pub struct Server { port: u16 }
pub enum State { On, Off }
pub trait Run { fn run(&self); }
pub type Alias = Server;
pub const MAX: u32 = 1;
pub static NAME: &str = "x";

impl Run for Server { fn run(&self) {} }

pub fn main() {}

mod util;
"""


@pytest.fixture
def sample_rust_project(tmp_path: Path) -> Path:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[dependencies]\nserde = "1.0"\n'
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text(_LIB_RS)
    (src / "util.rs").write_text("pub fn helper() {}\n")
    return tmp_path
