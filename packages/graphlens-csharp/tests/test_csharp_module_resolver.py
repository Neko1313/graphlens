from graphlens_csharp._module_resolver import (
    csproj_properties,
    first_csproj,
    internal_namespace_tops,
    iter_by_local,
    local_name,
    parse_xml,
)

CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">'
    "<PropertyGroup>"
    "<RootNamespace>Acme.Billing</RootNamespace>"
    "<AssemblyName>Acme.Billing.Api</AssemblyName>"
    "</PropertyGroup>"
    "</Project>"
)


# ---------------------------------------------------------------------------
# local_name / parse_xml / iter_by_local
# ---------------------------------------------------------------------------


def test_local_name_strips_namespace():
    assert local_name("{http://ms/2003}PropertyGroup") == "PropertyGroup"
    assert local_name("PackageReference") == "PackageReference"


def test_parse_xml_ok(tmp_path):
    p = tmp_path / "a.xml"
    p.write_text("<Root><Child/></Root>")
    root = parse_xml(p)
    assert root is not None
    assert local_name(root.tag) == "Root"


def test_parse_xml_bad_returns_none(tmp_path):
    p = tmp_path / "bad.xml"
    p.write_text("<not xml <<<")
    assert parse_xml(p) is None


def test_parse_xml_missing_returns_none(tmp_path):
    assert parse_xml(tmp_path / "nope.xml") is None


def test_iter_by_local_matches_regardless_of_ns(tmp_path):
    p = tmp_path / "a.xml"
    p.write_text(
        '<Project xmlns="http://ms/2003">'
        '<ItemGroup><PackageReference Include="X"/></ItemGroup>'
        "</Project>"
    )
    root = parse_xml(p)
    refs = list(iter_by_local(root, "PackageReference"))
    assert len(refs) == 1
    assert refs[0].get("Include") == "X"


# ---------------------------------------------------------------------------
# first_csproj / csproj_properties
# ---------------------------------------------------------------------------


def test_first_csproj_none_when_absent(tmp_path):
    assert first_csproj(tmp_path) is None


def test_csproj_properties_reads_all(tmp_path):
    (tmp_path / "App.csproj").write_text(CSPROJ)
    props = csproj_properties(tmp_path)
    assert props["RootNamespace"] == "Acme.Billing"
    assert props["AssemblyName"] == "Acme.Billing.Api"


def test_csproj_properties_empty_without_csproj(tmp_path):
    assert csproj_properties(tmp_path) == {}


def test_csproj_properties_empty_on_bad_xml(tmp_path):
    (tmp_path / "App.csproj").write_text("<<< bad")
    assert csproj_properties(tmp_path) == {}


def test_csproj_properties_skips_empty_tag(tmp_path):
    (tmp_path / "App.csproj").write_text(
        "<Project><PropertyGroup>"
        "<RootNamespace></RootNamespace>"
        "<AssemblyName>Real.Name</AssemblyName>"
        "</PropertyGroup></Project>"
    )
    props = csproj_properties(tmp_path)
    assert "RootNamespace" not in props
    assert props["AssemblyName"] == "Real.Name"


# ---------------------------------------------------------------------------
# internal_namespace_tops
# ---------------------------------------------------------------------------


def test_internal_tops_from_root_namespace(tmp_path):
    (tmp_path / "App.csproj").write_text(CSPROJ)
    assert internal_namespace_tops(tmp_path) == {"Acme"}


def test_internal_tops_multiple_projects(tmp_path):
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    (a / "A.csproj").write_text(
        "<Project><PropertyGroup><RootNamespace>Foo.A</RootNamespace>"
        "</PropertyGroup></Project>"
    )
    (b / "B.csproj").write_text(
        "<Project><PropertyGroup><RootNamespace>Bar.B</RootNamespace>"
        "</PropertyGroup></Project>"
    )
    assert internal_namespace_tops(tmp_path) == {"Foo", "Bar"}


def test_internal_tops_uses_stem_when_no_namespace(tmp_path):
    (tmp_path / "Widgets.csproj").write_text(
        "<Project><PropertyGroup/></Project>"
    )
    assert internal_namespace_tops(tmp_path) == {"Widgets"}


def test_internal_tops_bad_xml_uses_stem(tmp_path):
    (tmp_path / "Broken.csproj").write_text("<<< not xml")
    assert internal_namespace_tops(tmp_path) == {"Broken"}


def test_internal_tops_skips_empty_namespace_tag(tmp_path):
    (tmp_path / "App.csproj").write_text(
        "<Project><PropertyGroup>"
        "<RootNamespace></RootNamespace>"
        "<AssemblyName>Acme.Api</AssemblyName>"
        "</PropertyGroup></Project>"
    )
    assert internal_namespace_tops(tmp_path) == {"Acme"}


def test_internal_tops_skips_obj_dir(tmp_path):
    (tmp_path / "App.csproj").write_text(CSPROJ)
    obj = tmp_path / "obj"
    obj.mkdir()
    (obj / "Gen.csproj").write_text(
        "<Project><PropertyGroup><RootNamespace>Zzz</RootNamespace>"
        "</PropertyGroup></Project>"
    )
    assert internal_namespace_tops(tmp_path) == {"Acme"}
