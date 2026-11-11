from datetime import datetime, timedelta
from types import SimpleNamespace

from dara.server import api_router
from dara.server.api_router import (
    _fit_quality,
    _phase_refinement_details,
    _refined_phase_metadata,
)


def _refinement():
    phases = {
        "A": SimpleNamespace(
            gewicht=(2.0, 0.1), a=(0.4, 0.001), b=None, c=None,
            alpha=None, beta=None, gamma=None, xray_density=4.2,
            rphase=3.1, model_extra={"GrainSize(1,1,1)": (52.0, 3.0)},
        ),
        "B": SimpleNamespace(
            gewicht=(1.0, 0.1), a=0.5, b=None, c=None,
            alpha=None, beta=None, gamma=None, xray_density=5.1,
            rphase=4.2, model_extra={},
        ),
    }
    lst = SimpleNamespace(
        rp=3.0, rpb=4.0, r=5.0, rwp=6.0, rexp=3.0,
        d=1.8, rho=7.0, num_steps=12, phases_results=phases,
    )
    return SimpleNamespace(
        lst_data=lst,
        plot_data=SimpleNamespace(x=[10.0, 20.0, 30.0]),
    )


def test_fit_quality_and_phase_details_use_reported_values():
    result = _refinement()
    quality = _fit_quality(result)
    assert quality["gof"] == 2.0
    assert quality["chi_squared"] == 4.0
    assert quality["two_theta_min"] == 10.0
    assert quality["data_points"] == 3

    details = _phase_refinement_details(result)
    assert round(details["A"]["weight_percent"], 6) == round(200 / 3, 6)
    assert details["A"]["weight_esd_percent"] is not None
    assert details["A"]["lattice_parameters"]["a"]["value"] == 4.0
    assert details["A"]["lattice_parameters"]["a"]["esd"] == 0.01
    assert details["A"]["grain_sizes"][0]["value"] == 52.0


def test_unrefined_start_value_grain_size_is_not_reported():
    result = _refinement()
    # BGMN prints the same start-value GrainSize (no esd) for phases whose B1 stayed fixed
    result.lst_data.phases_results["B"].model_extra = {"GrainSize(1,1,1)": 84.1811, "B1": 0.005}
    assert _phase_refinement_details(result)["B"]["grain_sizes"] == []
    # a refined B1 without an esd on the size still counts as a result
    result.lst_data.phases_results["B"].model_extra = {"GrainSize(1,1,1)": 84.1811, "B1": (0.01, 0.002)}
    assert _phase_refinement_details(result)["B"]["grain_sizes"][0]["value"] == 84.1811


PH6NO4 = "PH6NO4_19_(cod_9007583)-3"
NA1267 = "Na12.67P4O64.67_165_(cod_9007527)-0"
LISTING = """BGMN
Local parameters and GOALs for phase PH6NO4_19_(cod_9007583)-3
SpacegroupNo=19
UNIT=NM
A=0.75578+-0.00047
B=0.764980
C=0.76914+-0.00017
k1=1.00000
k2=0.0000030+-0.0000024
B1=0.00243+-0.00077
GrainSize(1,1,1)=131+-41
GEWICHT=SPHAR2, MeanValue(GEWICHT)=0.0936961
Local parameters and GOALs for phase Na12.67P4O64.67_165_(cod_9007527)-0
SpacegroupNo=165
UNIT=NM
k1=UNDEF
k2=0.000289+-0.000034
B1=0
GrainSize(1,1,1)=ERROR
GEWICHT=SPHAR4, MeanValue(GEWICHT)=0.269123
"""
SPECS = {"lattice_range": 0.02, "k1": "0_0^1", "k2": "0_0^0.001", "b1": "0_0^0.05"}


def _listed_refinement(raw_lst=LISTING):
    phases = {
        PH6NO4: SimpleNamespace(
            gewicht=(0.0937, 0.004), a=(0.75578, 0.00047), b=0.76498, c=(0.76914, 0.00017),
            alpha=None, beta=None, gamma=None, xray_density=1.7, rphase=15.2, unit="NM",
            model_extra={"GrainSize(1,1,1)": (131.0, 41.0), "B1": (0.00243, 0.00077)},
        ),
        NA1267: SimpleNamespace(
            gewicht=(0.269, 0.01), a=None, b=None, c=None, alpha=None, beta=None, gamma=None,
            xray_density=1.55, rphase=23.9, unit="NM", model_extra={"B1": 0},
        ),
    }
    lst = SimpleNamespace(rp=3.0, rpb=4.0, r=5.0, rwp=21.1, rexp=2.9, d=1.8, rho=7.0,
                          num_steps=12, phases_results=phases, raw_lst=raw_lst)
    return SimpleNamespace(lst_data=lst, plot_data=SimpleNamespace(x=[10.0, 20.0, 30.0]))


def _reference(lattice_abc, angles):
    return SimpleNamespace(
        filename=NA1267,
        to_structure=lambda: SimpleNamespace(lattice=SimpleNamespace(abc=lattice_abc, angles=angles)),
    )


def test_phase_details_carry_status_size_strain_and_lattice_states():
    details = _phase_refinement_details(
        _listed_refinement(),
        spec=lambda name, key: SPECS[key],
        references=[_reference((11.89, 11.89, 12.671), (90.0, 90.0, 120.0))],
    )
    ph = details[PH6NO4]
    assert ph["refinement_status"]["level"] == "limited"
    assert ph["refinement_status"]["codes"] == ["lattice_at_limit"]
    assert {key: item["state"] for key, item in ph["lattice_parameters"].items()} == {
        "a": "refined", "b": "at_limit", "c": "refined",
    }
    assert ph["size_strain"]["strain_state"] == "poorly_determined"
    assert ph["size_strain"]["size_nm"]["value"] == 131.0

    na = details[NA1267]
    assert na["refinement_status"]["level"] == "limited"
    assert na["refinement_status"]["codes"] == ["lattice_fixed", "size_not_resolvable"]
    assert na["refinement_status"]["undefined"] == {"k1": "UNDEF", "GrainSize(1,1,1)": "ERROR"}
    assert na["lattice_parameters"]["a"] == {
        "value": 11.89, "esd": None, "state": "fixed_reference", "unit": "\u00c5",
    }
    assert na["lattice_parameters"]["gamma"]["value"] == 120.0
    assert na["size_strain"]["size_state"] == "not_resolvable"
    assert na["crystallite_size_status"] == "not_resolvable"
    # without the reference the lattice stays empty, but the status is still given
    bare = _phase_refinement_details(_listed_refinement(), spec=lambda name, key: SPECS[key])
    assert bare[NA1267]["lattice_parameters"] == {}
    assert "lattice_fixed" in bare[NA1267]["refinement_status"]["codes"]


def test_stored_results_without_a_listing_still_work():
    details = _phase_refinement_details(_refinement())
    assert "refinement_status" not in details["A"]
    assert details["A"]["lattice_parameters"]["a"]["state"] == "refined"  # has an e.s.d.
    assert details["B"]["lattice_parameters"]["a"]["state"] == "at_limit"  # none: no listing to tell
    # a listing that lacks the phase block is also left alone
    other = _phase_refinement_details(_listed_refinement("BGMN\nnothing here\n"))
    assert "refinement_status" not in other[PH6NO4]


def test_undefined_key_fails_the_phase():
    listing = LISTING.replace("A=0.75578+-0.00047", "A=UNDEF")
    details = _phase_refinement_details(_listed_refinement(listing), spec=lambda n, k: SPECS[k])
    assert details[PH6NO4]["refinement_status"]["level"] == "failed"
    assert details[PH6NO4]["size_strain"]["size_state"] == "failed"


def test_phase_metadata_names_the_database_setting():
    hematite = SimpleNamespace(spacegroup_no=15, hermann_mauguin="C12/c1")
    quartz = SimpleNamespace(spacegroup_no=152, hermann_mauguin="P3_121")
    refinement = SimpleNamespace(
        lst_data=SimpleNamespace(phases_results={
            "Fe2O3_167_(cod_2108027)-0": hematite, "SiO2_152_(cod_1)-None": quartz, "own.cif": quartz,
        })
    )
    meta = _refined_phase_metadata(refinement)
    fe = meta["Fe2O3_167_(cod_2108027)-0"]
    assert fe["spacegroup_number"] == 15 and fe["database_spacegroup_number"] == 167
    assert fe["database_spacegroup_symbol"] == "R-3c" and "subgroup setting of R-3c" in fe["setting_note"]
    assert "setting_note" not in meta["SiO2_152_(cod_1)-None"]
    assert meta["SiO2_152_(cod_1)-None"]["database_spacegroup_number"] == 152
    assert "database_spacegroup_number" not in meta["own.cif"]


def test_fit_quality_reports_the_sample_displacement():
    refinement = _refinement()
    refinement.lst_data.EPS2 = (-0.0019, 0.0001)
    assert _fit_quality(refinement)["sample_displacement_eps2"] == -0.0019
    assert _fit_quality(_refinement())["sample_displacement_eps2"] is None


def test_completed_task_without_a_final_result_is_a_payload_not_a_404():
    start = datetime(2026, 11, 1, 10, 0, 0)
    job = {
        "status": "COMPLETED", "job": {"name": "x.xy"}, "submitted_time": start,
        "start_time": start, "end_time": start + timedelta(seconds=90),
    }
    document = SimpleNamespace(
        final_result=None, search_summary={"stage": "done"}, candidate_summary={"n": 3},
        precursors=[], elements=["Fe"],
    )
    payload = api_router.summarize_document(job, document, persist_summary=False)
    assert payload["no_result"] is True and payload["final_result"] is None
    assert payload["status"] == "COMPLETED" and payload["runtime_s"] == 90.0
    assert payload["search_summary"] == {"stage": "done"} and payload["candidate_summary"] == {"n": 3}
    assert "fits the pattern" in payload["message"]
