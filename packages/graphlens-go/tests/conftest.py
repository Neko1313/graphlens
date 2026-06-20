"""Shared fixtures for graphlens-go tests."""

from pathlib import Path

import pytest

_MAIN_GO = """package main

import (
\t"fmt"
\t"github.com/pkg/errors"
\t"example.com/demo/util"
)

type Greeter struct { name string }

type Speaker interface { Say() string }

type Alias = Greeter

const Version = "1.0"

var Global int

func (g Greeter) Hello() string { return g.name }

func main() { fmt.Println(errors.New(util.Tag)) }
"""


@pytest.fixture
def sample_go_project(tmp_path: Path) -> Path:
    (tmp_path / "go.mod").write_text(
        "module example.com/demo\n\n"
        "go 1.22\n\n"
        "require (\n\tgithub.com/pkg/errors v0.9.1\n)\n"
    )
    (tmp_path / "main.go").write_text(_MAIN_GO)
    util = tmp_path / "util"
    util.mkdir()
    (util / "util.go").write_text(
        'package util\n\nconst Tag = "u"\n\nfunc Help() {}\n'
    )
    return tmp_path
