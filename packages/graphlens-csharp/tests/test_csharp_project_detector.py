from graphlens_csharp._project_detector import (
    detect_project_name,
    find_csharp_roots,
    is_csharp_project,
)

CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">'
    "<PropertyGroup><AssemblyName>Acme.App</AssemblyName></PropertyGroup>"
    "</Project>"
)


# ---------------------------------------------------------------------------
# is_csharp_project
# ---------------------------------------------------------------------------


def test_is_csharp_project_by_csproj(tmp_path):
    (tmp_path / "App.csproj").write_text(CSPROJ)
    assert is_csharp_project(tmp_path)


def test_is_csharp_project_by_nested_csproj(tmp_path):
    nested = tmp_path / "src" / "App"
    nested.mkdir(parents=True)
    (nested / "App.csproj").write_text(CSPROJ)
    assert is_csharp_project(tmp_path)


def test_is_csharp_project_fallback_to_cs_file(tmp_path):
    (tmp_path / "Program.cs").write_text("class P {}")
    assert is_csharp_project(tmp_path)


def test_is_csharp_project_false_when_no_markers(tmp_path):
    (tmp_path / "readme.txt").write_text("nope")
    assert not is_csharp_project(tmp_path)


def test_is_csharp_project_ignores_csproj_in_excluded_dir(tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "Fake.csproj").write_text(CSPROJ)
    assert not is_csharp_project(tmp_path)


def test_is_csharp_project_ignores_cs_in_excluded_dir(tmp_path):
    obj = tmp_path / "obj"
    obj.mkdir()
    (obj / "Generated.cs").write_text("class G {}")
    assert not is_csharp_project(tmp_path)


# ---------------------------------------------------------------------------
# find_csharp_roots
# ---------------------------------------------------------------------------


def test_find_roots_single(tmp_path):
    (tmp_path / "App.csproj").write_text(CSPROJ)
    assert find_csharp_roots(tmp_path) == [tmp_path]


def test_find_roots_monorepo_multiple(tmp_path):
    a = tmp_path / "ServiceA"
    b = tmp_path / "ServiceB"
    a.mkdir()
    b.mkdir()
    (a / "A.csproj").write_text(CSPROJ)
    (b / "B.csproj").write_text(CSPROJ)
    assert set(find_csharp_roots(tmp_path)) == {a, b}


def test_find_roots_root_and_nested(tmp_path):
    (tmp_path / "Root.csproj").write_text(CSPROJ)
    child = tmp_path / "Child"
    child.mkdir()
    (child / "Child.csproj").write_text(CSPROJ)
    assert set(find_csharp_roots(tmp_path)) == {tmp_path, child}


def test_find_roots_skips_bin_obj(tmp_path):
    (tmp_path / "App.csproj").write_text(CSPROJ)
    obj = tmp_path / "obj"
    obj.mkdir()
    (obj / "Generated.csproj").write_text(CSPROJ)
    assert find_csharp_roots(tmp_path) == [tmp_path]


def test_find_roots_fallback_when_no_markers(tmp_path):
    (tmp_path / "loose.cs").write_text("class X {}")
    assert find_csharp_roots(tmp_path) == [tmp_path]


# ---------------------------------------------------------------------------
# detect_project_name
# ---------------------------------------------------------------------------


def test_detect_name_from_assembly_name(tmp_path):
    (tmp_path / "App.csproj").write_text(CSPROJ)
    assert detect_project_name(tmp_path) == "Acme.App"


def test_detect_name_from_package_id(tmp_path):
    (tmp_path / "App.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk">'
        "<PropertyGroup><PackageId>Acme.Package</PackageId></PropertyGroup>"
        "</Project>"
    )
    assert detect_project_name(tmp_path) == "Acme.Package"


def test_detect_name_from_csproj_stem(tmp_path):
    (tmp_path / "Billing.Api.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup/></Project>'
    )
    assert detect_project_name(tmp_path) == "Billing.Api"


def test_detect_name_falls_back_to_dir(tmp_path):
    assert detect_project_name(tmp_path) == tmp_path.name
