import pytest

from app.services.resolver import TargetResolver
from tests.conftest import FakeNed, FakeSimbad


def test_resolve_by_name(resolver):
    t = resolver.resolve("NGC 1365")
    assert t.input_kind == "name"
    assert t.main_id == "NGC  1365"
    assert t.ned_name == "NGC 1365"
    assert t.coordinates.ra_deg == pytest.approx(53.40152)
    assert t.coordinates.dec_deg == pytest.approx(-36.14040)
    assert t.coordinates.ra_hms.startswith("03:33:36")
    assert t.coordinates.source == "SIMBAD"
    assert t.kinematics.redshift == pytest.approx(0.005457)
    assert t.kinematics.velocity_kms == pytest.approx(1636.0)
    assert t.kinematics.velocity_err_kms == pytest.approx(1.0)
    assert t.kinematics.measurement_type == "v"
    assert t.morphology.type == "SB(s)b"
    assert t.morphology.object_type == "Sy1"
    assert t.morphology.object_type_label == "Seyfert 1 Galaxy"
    assert t.morphology.major_axis_arcmin == pytest.approx(11.22)
    assert t.distances[0].value == pytest.approx(18.1)
    assert t.distances[0].unit == "Mpc"
    assert "FCC 121" in t.aliases
    assert t.aliases[0] == "NGC  1365"
    assert set(t.sources) == {"SIMBAD", "NED"}
    assert t.warnings == []
    assert not t.cached
    # 4x the major axis, in degrees
    assert t.suggested_fov_deg == pytest.approx(4 * 11.22 / 60)


def test_environment_membership_and_neighbours(resolver):
    t = resolver.resolve("NGC 1365")
    member_names = {m.name for m in t.environment.memberships}
    assert "FCC 121" in member_names
    assert "LGG  94" in member_names
    catalogs = {m.catalog for m in t.environment.memberships}
    assert any("Fornax" in c for c in catalogs)
    # the target itself is filtered out of the nearby list
    nearby = t.environment.nearby_groups
    assert [n.name for n in nearby] == ["NAME Fornax Cluster"]
    assert nearby[0].object_type == "ClG"
    assert nearby[0].separation_arcmin == pytest.approx(54.0)
    assert nearby[0].relation == "nearby"


def test_resolve_by_coordinates_uses_cone_search(resolver):
    t = resolver.resolve("53.4015, -36.1404")
    assert t.input_kind == "coordinates"
    assert t.main_id == "NGC  1365"
    assert ("basic_near", pytest.approx(53.4015), pytest.approx(-36.1404), 30 / 3600) in [
        c for c in resolver.simbad.calls if c[0] == "basic_near"
    ]
    # NED is queried with the SIMBAD main_id, not the raw coordinates
    assert resolver.ned.calls == [("query_object", "NGC  1365")]


def test_coordinates_with_no_catalog_match(settings):
    r = TargetResolver(settings=settings, simbad=FakeSimbad(basic=None), ned=FakeNed(row=None))
    t = r.resolve("10.0 10.0")
    assert t.main_id is None
    assert t.coordinates.source == "user input"
    assert t.coordinates.ra_deg == pytest.approx(10.0)
    assert any("No SIMBAD object" in w for w in t.warnings)
    assert t.kinematics.redshift is None
    assert t.suggested_fov_deg == 0.25


def test_simbad_outage_falls_back_to_ned(settings):
    r = TargetResolver(settings=settings, simbad=FakeSimbad(fail=True), ned=FakeNed())
    t = r.resolve("NGC 1365")
    assert t.main_id is None
    assert t.coordinates.source == "NED"
    assert t.kinematics.source == "NED"
    assert t.kinematics.redshift == pytest.approx(0.005457)
    assert t.morphology.object_type == "G"
    assert t.sources == ["NED"]
    assert any(w.startswith("SIMBAD unavailable") for w in t.warnings)


def test_ned_outage_is_a_warning_not_an_error(settings):
    r = TargetResolver(settings=settings, simbad=FakeSimbad(), ned=FakeNed(fail=True))
    t = r.resolve("NGC 1365")
    assert t.main_id == "NGC  1365"
    assert t.ned_name is None
    assert any(w.startswith("NED unavailable") for w in t.warnings)


def test_unresolvable_name_raises_lookup_error(settings, monkeypatch):
    from astropy.coordinates import SkyCoord

    def boom(name):
        raise ValueError("Unable to find coordinates for name")

    monkeypatch.setattr(SkyCoord, "from_name", staticmethod(boom))
    r = TargetResolver(settings=settings, simbad=FakeSimbad(basic=None), ned=FakeNed(row=None))
    with pytest.raises(LookupError):
        r.resolve("Definitely Not An Object")


def test_cache_hit_marks_cached(resolver):
    first = resolver.resolve("NGC 1365")
    second = resolver.resolve("ngc   1365")
    assert not first.cached
    assert second.cached
    assert len([c for c in resolver.simbad.calls if c[0] == "basic_by_name"]) == 1
    third = resolver.resolve("NGC 1365", use_cache=False)
    assert not third.cached


def test_empty_query_rejected(resolver):
    with pytest.raises(ValueError):
        resolver.resolve("   ")


def test_poor_simbad_redshift_defers_to_ned(settings):
    from tests.conftest import NGC1365_BASIC

    poor = dict(
        NGC1365_BASIC,
        rvz_redshift=0.005091,
        rvz_radvel=1522.2,
        rvz_err=0.0005,
        rvz_type="z",
        rvz_qual="D",
    )
    r = TargetResolver(settings=settings, simbad=FakeSimbad(basic=poor), ned=FakeNed())
    t = r.resolve("NGC 1365")
    assert t.kinematics.source.startswith("NED")
    assert t.kinematics.velocity_kms == pytest.approx(1636.0)
    assert t.kinematics.quality == "D"
    assert t.kinematics.ned_redshift == pytest.approx(0.005457)
    # with a good SIMBAD value, SIMBAD still wins but NED is reported alongside
    good = TargetResolver(settings=settings, simbad=FakeSimbad(), ned=FakeNed()).resolve("NGC 1365")
    assert good.kinematics.source == "SIMBAD" and good.kinematics.ned_velocity_kms == 1636
