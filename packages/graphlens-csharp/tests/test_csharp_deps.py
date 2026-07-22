from graphlens_csharp._deps import (
    CSHARP_DEFAULT_DEP_PARSERS,
    CsprojDepsParser,
    DirectoryPackagesPropsDepsParser,
    PackagesConfigDepsParser,
    _top_segment,
    get_stdlib_names,
)

# ---------------------------------------------------------------------------
# _top_segment
# ---------------------------------------------------------------------------


def test_top_segment_dotted():
    assert _top_segment("Newtonsoft.Json") == "newtonsoft"


def test_top_segment_single():
    assert _top_segment("Serilog") == "serilog"


def test_top_segment_non_string():
    assert _top_segment(None) == ""


# ---------------------------------------------------------------------------
# CsprojDepsParser
# ---------------------------------------------------------------------------


def test_csproj_can_parse(tmp_path):
    (tmp_path / "App.csproj").write_text("<Project/>")
    assert CsprojDepsParser().can_parse(tmp_path)


def test_csproj_can_parse_false(tmp_path):
    assert not CsprojDepsParser().can_parse(tmp_path)


def test_csproj_parse_package_references(tmp_path):
    (tmp_path / "App.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>'
        '<PackageReference Include="Newtonsoft.Json" Version="13.0.1"/>'
        '<PackageReference Include="Serilog" Version="3.0.0"/>'
        '<PackageReference Update="Microsoft.Extensions.Logging"/>'
        "<PackageReference/>"  # malformed: no Include/Update — skipped
        "</ItemGroup></Project>"
    )
    assert CsprojDepsParser().parse(tmp_path) == frozenset(
        {"newtonsoft", "serilog", "microsoft"}
    )


def test_csproj_parse_bad_xml_is_skipped(tmp_path):
    (tmp_path / "App.csproj").write_text("<<< bad")
    assert CsprojDepsParser().parse(tmp_path) == frozenset()


# ---------------------------------------------------------------------------
# PackagesConfigDepsParser
# ---------------------------------------------------------------------------


def test_packages_config_can_parse(tmp_path):
    (tmp_path / "packages.config").write_text("<packages/>")
    assert PackagesConfigDepsParser().can_parse(tmp_path)
    assert not PackagesConfigDepsParser().can_parse(tmp_path / "sub")


def test_packages_config_parse(tmp_path):
    (tmp_path / "packages.config").write_text(
        '<packages><package id="NLog" version="5.0.0"/>'
        '<package id="AutoMapper.Extras" version="1.0.0"/>'
        "<package/></packages>"  # malformed: no id — skipped
    )
    assert PackagesConfigDepsParser().parse(tmp_path) == frozenset(
        {"nlog", "automapper"}
    )


def test_packages_config_parse_bad_xml(tmp_path):
    (tmp_path / "packages.config").write_text("<<<")
    assert PackagesConfigDepsParser().parse(tmp_path) == frozenset()


# ---------------------------------------------------------------------------
# DirectoryPackagesPropsDepsParser (Central Package Management)
# ---------------------------------------------------------------------------


def test_directory_packages_can_parse(tmp_path):
    (tmp_path / "Directory.Packages.props").write_text("<Project/>")
    assert DirectoryPackagesPropsDepsParser().can_parse(tmp_path)
    assert not DirectoryPackagesPropsDepsParser().can_parse(tmp_path / "x")


def test_directory_packages_parse(tmp_path):
    (tmp_path / "Directory.Packages.props").write_text(
        "<Project><ItemGroup>"
        '<PackageVersion Include="Dapper" Version="2.0.0"/>'
        '<PackageVersion Include="xunit.assert" Version="2.4.0"/>'
        "<PackageVersion/>"  # malformed: no Include — skipped
        "</ItemGroup></Project>"
    )
    assert DirectoryPackagesPropsDepsParser().parse(tmp_path) == frozenset(
        {"dapper", "xunit"}
    )


def test_directory_packages_parse_bad_xml(tmp_path):
    (tmp_path / "Directory.Packages.props").write_text("<<<")
    assert DirectoryPackagesPropsDepsParser().parse(tmp_path) == frozenset()


# ---------------------------------------------------------------------------
# Stdlib names / default parsers
# ---------------------------------------------------------------------------


def test_get_stdlib_names():
    assert "System" in get_stdlib_names()


def test_default_parsers_present():
    types = {type(p) for p in CSHARP_DEFAULT_DEP_PARSERS}
    assert types == {
        CsprojDepsParser,
        PackagesConfigDepsParser,
        DirectoryPackagesPropsDepsParser,
    }
