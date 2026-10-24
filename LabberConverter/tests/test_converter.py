from pathlib import Path
import json
import shutil
import sys
import zipfile

import h5py
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import converter
from converter import convert_batch, discover, merge_database, reindex, initialize_database, ConversionError, available_path


def nc_file(path, *, date="2026-10-20T18:15:00", extra=False):
    signal=np.array([[1+2j,3+4j,5+6j], [7-8j,9-10j,11-12j],
                     [complex(np.nan,0)]*3, [complex(np.nan,0)]*3])
    with h5py.File(path,"w") as file:
        file.attrs.update(schema="labcontrol-nc/1",layout="grid",created=date,points=np.array([2]),
                          planned_points=np.array([4]),complete=np.array([0]),
                          project="mirror",user="QEL",tags="VNA Sweep, 電流",
                          labcontrol=json.dumps({"snapshot":{"DC3":{"level":.166}}}))
        axis=file.create_dataset("Current",data=[166.308,166.3085,166.309,166.3095])
        axis.make_scale("Current");axis.attrs.update(long_name="Average Current",units="mA")
        freq=file.create_dataset("Frequency",data=[5e9,5e9+2000,5e9+4000])
        freq.make_scale("Frequency");freq.attrs.update(long_name="Frequency",units="Hz")
        trace=file.create_dataset("S21",data=signal)
        trace.dims[0].attach_scale(axis);trace.dims[1].attach_scale(freq)
        trace.attrs.update(long_name="VNA - S21",units="")
        ts=file.create_dataset("timestamp",data=[1792491300.,1792491301.,np.nan,np.nan])
        ts.dims[0].attach_scale(axis)
        file.create_dataset("retained",data=np.array([0,1,0,0],dtype=np.int8))
        if extra:file.create_dataset("unexpected_scalar_signal",data=[1.,2.,3.,4.])
    return signal[:2]


def test_partial_complex_data_units_and_acquisition_status(tmp_path):
    source=tmp_path/"one.nc"
    original=nc_file(source)
    before=source.read_bytes()
    report=convert_batch([source],tmp_path/"out")
    assert report["ok"]==1 and not report["errors"]
    record=report["files"][0]
    assert record["points"]==2  # NaN+0j allocation must not count as measured data.
    path=Path(record["output"])
    assert path.relative_to(tmp_path/"out")==Path("2026/10/Data_1020")/tmp_path.name/"one.hdf5"
    with h5py.File(path,"r") as file:
        np.testing.assert_array_equal(file["Traces/VNA - S21"][:,0,:].T,original.real)
        np.testing.assert_array_equal(file["Traces/VNA - S21"][:,1,:].T,original.imag)
        np.testing.assert_array_equal(file["Data/Data"][0,1,:],[166.308,166.3085])
        assert file["Channels"][1]["unitPhys"]==b"mA"
        assert not file["Data"].attrs["Completed"]
        assert file["Conversion"].attrs["planned_points"]==4
        np.testing.assert_array_equal(file["Conversion/retained"],[0,1])
        assert len(file["Conversion/planned_axis"])==4
        assert h5py.check_enum_dtype(file["Step list"].dtype["step_unit"])
        assert json.loads(file["Conversion/source_attributes_json"].asstr()[()])["user"]=="QEL"
        assert json.loads(file["Conversion"].attrs["original_project_json"])==["mirror"]
    with h5py.File(tmp_path/"out/Database.hdf5","r") as file:
        assert {f"Project/10/1020/{tmp_path.name}","User/QEL","Tags/電流"}<=set(file["Labels"].asstr()[()])
    assert report["database_verified"] and report["database_created"]
    assert source.read_bytes()==before


def test_multiple_dates_keep_distinct_source_folders_and_never_overwrite(tmp_path):
    a=tmp_path/"a";b=tmp_path/"b";a.mkdir();b.mkdir()
    nc_file(a/"same.nc")
    nc_file(b/"same.nc")
    nc_file(b/"next.nc",date="2026-10-25T01:00:00")
    report=convert_batch([a,b],tmp_path/"out")
    assert report["ok"]==3
    paths={Path(r["output"]).relative_to(tmp_path/"out").as_posix() for r in report["files"]}
    assert paths=={"2026/10/Data_1020/a/same.hdf5","2026/10/Data_1020/b/same.hdf5","2026/10/Data_1025/b/next.hdf5"}
    first=(tmp_path/"out/2026/10/Data_1020/a/same.hdf5").read_bytes()
    convert_batch([a/"same.nc"],tmp_path/"out")
    assert (tmp_path/"out/2026/10/Data_1020/a/same.hdf5").read_bytes()==first


def test_zip_members_are_streamed_not_extracted_and_overlaps_deduplicate(tmp_path):
    source=tmp_path/"one.nc";nc_file(source)
    archive=tmp_path/"data.zip"
    with zipfile.ZipFile(archive,"w") as z:
        z.write(source,"Data_1020/one.nc")
        z.writestr("../../escaped.nc",b"ignore")
        z.writestr("__MACOSX/._one.nc",b"ignore")
    assert len(discover([archive,archive]))==1
    assert len(discover([source,tmp_path]))==2  # direct .nc plus one ZIP member
    report=convert_batch([archive],tmp_path/"out")
    assert report["ok"]==1
    assert Path(report["files"][0]["output"]).name=="one.hdf5"
    assert not (tmp_path.parent/"escaped.nc").exists()


def test_database_merge_preserves_original_labels_extra_nodes_and_backup(tmp_path):
    old=tmp_path/"Database.hdf5"
    with h5py.File(old,"w") as file:
        file.create_dataset("Labels",data=np.array(["Project/unused",""],dtype=object),dtype=h5py.string_dtype())
        file.create_group("Extra").attrs["keep"]=123
    before=old.read_bytes()
    merge_database(tmp_path,["Project/new","Project/new"])
    with h5py.File(old,"r") as file:
        assert list(file["Labels"].asstr()[()])==["Project/unused","","Project/new"]
        assert file["Extra"].attrs["keep"]==123
    assert not list(tmp_path.glob("*.bak"))                          # 0.4.2: not beside Database.hdf5
    backups=list((tmp_path/converter.BACKUP_DIR).glob("*.bak"))
    assert len(backups)==1 and backups[0].read_bytes()==before
    merge_database(tmp_path,["Project/new"])
    assert len(list((tmp_path/converter.BACKUP_DIR).glob("*.bak")))==1


def test_unsupported_readout_fails_explicitly_without_partial_output(tmp_path):
    source=tmp_path/"one.nc";nc_file(source,extra=True)
    report=convert_batch([source],tmp_path/"out")
    assert report["errors"]==1
    assert "unexpected_scalar_signal" in report["files"][0]["error"]
    assert not list((tmp_path/"out").glob("2026/**/*.hdf5"))


def test_invalid_existing_database_is_not_replaced(tmp_path):
    source=tmp_path/"one.nc";nc_file(source)
    out=tmp_path/"out";out.mkdir()
    database=out/"Database.hdf5";database.write_bytes(b"not hdf5")
    with pytest.raises(OSError):convert_batch([source],out)
    assert database.read_bytes()==b"not hdf5"
    assert not (out/".labber-converter.lock").exists()


def test_copy_labber_preserves_values_and_source_and_reindex_keeps_unused_labels(tmp_path):
    source=tmp_path/"one.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"first")
    original=Path(first["files"][0]["output"])
    before=original.read_bytes()
    second=convert_batch([original],tmp_path/"second")
    copy=Path(second["files"][0]["output"])
    with h5py.File(original,"r") as source_file,h5py.File(copy,"r") as output_file:
        for name in ["Data/Data","Traces/VNA - S21","Traces/VNA - S21_t0dt","Time stamp"]:
            if name in source_file:
                np.testing.assert_array_equal(source_file[name][()],output_file[name][()])
        assert list(source_file["Tags"].attrs["Project"])==list(output_file["Tags"].attrs["Project"])
    assert original.read_bytes()==before
    merge_database(tmp_path/"second",["Project/unused"])
    result=reindex(tmp_path/"second")
    assert result["files"]==1 and not result["errors"]
    with h5py.File(tmp_path/"second/Database.hdf5","r") as file:
        assert "Project/unused" in file["Labels"].asstr()[()]


def test_cancellation_leaves_completed_database_consistent(tmp_path):
    a=tmp_path/"a.nc";b=tmp_path/"b.nc";nc_file(a);nc_file(b)
    calls=[]
    report=convert_batch([a,b],tmp_path/"out",cancel=lambda:bool(calls),progress=lambda d,t,n:calls.append(n) if d==1 else None)
    assert report["ok"]==1 and report["cancelled"]
    assert not (tmp_path/"out/.labber-converter.lock").exists()


def test_nc_inside_output_folder_can_be_converted_without_reimporting_hdf5(tmp_path):
    source=tmp_path/"one.nc";nc_file(source)
    first=convert_batch([tmp_path],tmp_path)
    assert first["ok"]==1
    second=convert_batch([tmp_path],tmp_path)
    # The original NC is one input; its previous HDF5 is not recursively copied.
    assert second["ok"]==1 and len(second["files"])==1


def test_suffix_uses_highest_matching_number_and_preserves_source_stem(tmp_path):
    for name in ["foo.hdf5","foo_1.hdf5","foo_3.hdf5","foo_11.hdf5","foo_other_99.hdf5"]:
        (tmp_path/name).write_bytes(b"existing")
    assert available_path(tmp_path/"foo.hdf5").name=="foo_12.hdf5"
    # The _1 in an original measurement name is not stripped as if it were a duplicate suffix.
    (tmp_path/"foo_1_2.hdf5").write_bytes(b"existing")
    assert available_path(tmp_path/"foo_1.hdf5").name=="foo_1_3.hdf5"
    assert available_path(tmp_path/"different.hdf5").name=="different.hdf5"


def test_keep_both_callback_preview_matches_new_filename_and_labber_name(tmp_path):
    source=tmp_path/"one.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    old=Path(first["files"][0]["output"]);before=old.read_bytes()
    (old.parent/"one_1.hdf5").write_bytes(b"one")
    (old.parent/"one_3.hdf5").write_bytes(b"three")
    requests=[]
    def keep(incoming,destination,suggestion):
        requests.append((incoming.name,destination,suggestion.name))
        return "keep"
    report=convert_batch([source],tmp_path/"out",on_conflict=keep)
    assert requests==[("one.nc",old,"one_4.hdf5")]
    assert report["files"][0]["conflict_action"]=="keep"
    new=Path(report["files"][0]["output"])
    assert new.name=="one_4.hdf5" and old.read_bytes()==before
    with h5py.File(new,"r") as file:assert file.attrs["log_name"]=="one_4"


def test_skip_does_not_convert_or_change_existing_output(tmp_path,monkeypatch):
    source=tmp_path/"one.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    old=Path(first["files"][0]["output"]);before=old.read_bytes()
    def forbidden(*args,**kwargs):raise AssertionError("Skip must not convert")
    monkeypatch.setattr(converter,"write_nc",forbidden)
    report=convert_batch([source],tmp_path/"out",on_conflict="skip")
    assert report["ok"]==0 and report["errors"]==0 and report["skipped"]==1
    assert old.read_bytes()==before
    assert sorted(p.name for p in old.parent.glob("*.hdf5"))==["one.hdf5"]


def test_replace_updates_only_selected_output_after_validation(tmp_path):
    source=tmp_path/"one.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    old=Path(first["files"][0]["output"])
    with h5py.File(source,"r+") as file:file["S21"][0,0]=777+888j
    source_before=source.read_bytes()
    report=convert_batch([source],tmp_path/"out",on_conflict="replace")
    assert report["ok"]==1 and report["files"][0]["output"]==str(old)
    assert report["files"][0]["conflict_action"]=="replace"
    with h5py.File(old,"r") as file:
        assert file["Traces/VNA - S21"][0,0,0]==777
        assert file["Traces/VNA - S21"][0,1,0]==888
    assert source.read_bytes()==source_before
    assert not list(old.parent.glob(".previous-output-*"))


@pytest.mark.parametrize("failure",["validation","database"])
def test_replace_failure_keeps_original_output(tmp_path,monkeypatch,failure):
    source=tmp_path/"one.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    old=Path(first["files"][0]["output"]);before=old.read_bytes()
    def fail(*args,**kwargs):raise OSError("simulated failure")
    if failure=="validation":
        monkeypatch.setattr(converter,"validate_nc",fail)
    else:
        merge=converter.merge_database
        def fail_merge(root,labels):
            if labels:raise OSError("simulated database failure")
            return merge(root,labels)
        monkeypatch.setattr(converter,"merge_database",fail_merge)
    report=convert_batch([source],tmp_path/"out",on_conflict="replace")
    assert report["errors"]==1 and report["ok"]==0
    assert old.read_bytes()==before
    assert not list(old.parent.glob(".*.tmp"))


def test_initialize_database_before_any_data_is_added(tmp_path):
    root=tmp_path/"My database"
    result=initialize_database(root)
    assert result["database_created"] and result["database_verified"]
    assert result["database_path"]==str(root/"Database.hdf5")
    with h5py.File(root/"Database.hdf5","r") as file:
        assert list(file)==["Labels"] and file["Labels"].ndim==1
        assert h5py.check_string_dtype(file["Labels"].dtype).encoding=="utf-8"
    before=(root/"Database.hdf5").read_bytes()
    assert not initialize_database(root)["database_created"]
    assert (root/"Database.hdf5").read_bytes()==before


@pytest.mark.parametrize("existing_database",[False,True])
@pytest.mark.parametrize("kind",["nc","native"])
def test_reference_structure_in_new_and_existing_databases(tmp_path,monkeypatch,existing_database,kind):
    root=tmp_path/"Mother folder"
    if existing_database:
        initialize_database(root)
        merge_database(root,["Project/08/0828","User/Existing user"])
    parent=tmp_path/"source database/2026/10/Data_1021"
    parent.mkdir(parents=True)
    source=parent/"a.nc"
    signal=nc_file(source)  # Metadata date differs: keep the supplied Data_1021 folder.
    if kind=="native":
        seed=convert_batch([source],tmp_path/"seed")
        native=parent/"a.hdf5"
        shutil.copyfile(seed["files"][0]["output"],native)
        source=native
    before=source.read_bytes()
    write=converter.write_nc
    def write_after_database_exists(*args,**kwargs):
        assert (root/"Database.hdf5").is_file()
        return write(*args,**kwargs)
    monkeypatch.setattr(converter,"write_nc",write_after_database_exists)
    report=convert_batch([source],root)
    assert report["ok"]==1 and report["errors"]==0
    output=root/"2026/10/Data_1021/a.hdf5"
    assert report["files"][0]["output"]==str(output)
    assert report["database_created"] is not existing_database
    assert list(root.rglob("Database.hdf5"))==[root/"Database.hdf5"]
    with h5py.File(output,"r") as file:
        assert list(file["Tags"].attrs["Project"])==["10/1021"]
        np.testing.assert_array_equal(file["Traces/VNA - S21"][:,0,:].T,signal.real)
        np.testing.assert_array_equal(file["Traces/VNA - S21"][:,1,:].T,signal.imag)
    with h5py.File(root/"Database.hdf5","r") as file:
        labels=set(file["Labels"].asstr()[()])
        assert "Project/10/1021" in labels
        if existing_database:assert {"Project/08/0828","User/Existing user"}<=labels
    assert source.read_bytes()==before


def test_generic_parent_and_child_folders_appear_in_filesystem_and_project(tmp_path):
    parent=tmp_path/"X/subfolder";parent.mkdir(parents=True)
    source=parent/"a.nc";nc_file(source)
    report=convert_batch([tmp_path/"X"],tmp_path/"out")
    assert report["ok"]==1
    output=Path(report["files"][0]["output"])
    assert output.relative_to(tmp_path/"out")==Path("2026/10/Data_1020/X/subfolder/a.hdf5")
    assert report["files"][0]["native_scan_path_warning"]
    with h5py.File(output,"r") as file:
        assert list(file["Tags"].attrs["Project"])==["10/1020/X/subfolder"]
    with h5py.File(tmp_path/"out/Database.hdf5","r") as file:
        assert "Project/10/1020/X/subfolder" in file["Labels"].asstr()[()]


def test_zip_preserves_native_date_tree_without_duplicate_parents(tmp_path):
    source=tmp_path/"a.nc";nc_file(source)
    archive=tmp_path/"export.zip"
    with zipfile.ZipFile(archive,"w") as file:
        file.write(source,"Original database/2026/10/Data_1021/a.nc")
    report=convert_batch([archive],tmp_path/"out")
    assert report["ok"]==1
    assert Path(report["files"][0]["output"]).relative_to(tmp_path/"out")==Path("2026/10/Data_1021/a.hdf5")
    assert report["files"][0]["project"]=="10/1021"


def test_native_file_directly_under_month_keeps_reference_layout(tmp_path):
    parent=tmp_path/"source/2026/08";parent.mkdir(parents=True)
    source=parent/"0812 RSMEP.nc";nc_file(source,date="2026-08-12T01:00:00")
    seed=convert_batch([source],tmp_path/"seed")
    original=Path(seed["files"][0]["output"])
    report=convert_batch([original],tmp_path/"out")
    assert report["ok"]==1
    assert Path(report["files"][0]["output"]).relative_to(tmp_path/"out")==Path("2026/08/0812 RSMEP.hdf5")
    assert report["files"][0]["project"]=="08/0812"


def test_project_prefix_and_old_native_projects_keep_source_hierarchy(tmp_path):
    parent=tmp_path/"Data_1021";parent.mkdir()
    source=parent/"a.nc";nc_file(source)
    seed=convert_batch([parent],tmp_path/"seed",project="My project")
    assert seed["files"][0]["project"]=="My project/10/1021"
    native=Path(seed["files"][0]["output"])
    with h5py.File(native,"r+") as file:
        del file["Tags"].attrs["Project"]
        file["Tags"].attrs.create("Project",["mirror","Other project"],dtype=h5py.string_dtype())
    before=native.read_bytes()
    report=convert_batch([native],tmp_path/"out")
    assert report["ok"]==1
    with h5py.File(report["files"][0]["output"],"r") as file:
        assert list(file["Tags"].attrs["Project"])==["mirror/10/1021","Other project/10/1021"]
    assert native.read_bytes()==before


@pytest.mark.parametrize("as_zip",[False,True])
def test_selected_month_tree_does_not_append_month_under_day(tmp_path,as_zip):
    month=tmp_path/"10";day=month/"Data_1013";day.mkdir(parents=True)
    source=day/"a.nc";signal=nc_file(source,date="2026-10-13T01:00:00")
    inputs=month
    if as_zip:
        inputs=tmp_path/"month.zip"
        with zipfile.ZipFile(inputs,"w") as archive:archive.write(source,"10/Data_1013/a.nc")
    report=convert_batch([inputs],tmp_path/"out")
    assert report["ok"]==1 and not report["errors"]
    output=tmp_path/"out/2026/10/Data_1013/a.hdf5"
    assert report["files"][0]["output"]==str(output)
    assert report["files"][0]["project"]=="10/1013"
    assert not report["files"][0]["native_scan_path_warning"]
    assert not (output.parent/"10").exists()
    with h5py.File(output,"r") as file:
        np.testing.assert_array_equal(file["Traces/VNA - S21"][:,0,:].T,signal.real)


@pytest.mark.parametrize("prefix",["","mirror/"])
def test_reimport_old_duplicate_month_layout_repairs_path_and_project(tmp_path,prefix):
    month=tmp_path/"10";day=month/"Data_1013";day.mkdir(parents=True)
    source=day/"a.nc";nc_file(source,date="2026-10-13T01:00:00")
    seed=convert_batch([month],tmp_path/"seed")
    bad=tmp_path/"old/2026/10/Data_1013/10/a.hdf5";bad.parent.mkdir(parents=True)
    shutil.copyfile(seed["files"][0]["output"],bad)
    with h5py.File(bad,"r+") as file:
        del file["Tags"].attrs["Project"]
        file["Tags"].attrs.create("Project",[prefix+"10/1013/10"],dtype=h5py.string_dtype())
    before=bad.read_bytes()
    report=convert_batch([bad],tmp_path/"out")
    assert report["ok"]==1 and not report["errors"]
    assert report["files"][0]["output"]==str(tmp_path/"out/2026/10/Data_1013/a.hdf5")
    assert report["files"][0]["project"]==prefix+"10/1013"
    assert bad.read_bytes()==before


# ---- 0.4.2 ----------------------------------------------------------------------------------
def point_nc(path, *, two_axes=False):
    """Lab Control writes layout=point when sweep points are not on a regular grid (asynchronous sweeps)."""
    signal=np.array([[1+1j,2+2j,3+3j],[4+4j,5+5j,6+6j],[7+7j,8+8j,9+9j]])
    with h5py.File(path,"w") as file:
        file.attrs.update(schema="labcontrol-nc/1",layout="point",created="2026-10-22T18:08:00",points=np.array([3]),
                          planned_points=np.array([3]),complete=np.array([1]),project="mirror",user="QEL",tags="2D電流異步",
                          labcontrol="{}")
        point=file.create_dataset("point",data=np.arange(3));point.make_scale("point")
        cur=file.create_dataset("Current",data=[0.2,0.1,0.3]);cur.dims[0].attach_scale(point)
        cur.attrs.update(long_name="Average Current",units="mA")
        if two_axes:
            other=file.create_dataset("Field",data=[1.,2.,3.]);other.dims[0].attach_scale(point)
        freq=file.create_dataset("Frequency",data=[5e9,5e9+1e3,5e9+2e3]);freq.make_scale("Frequency")
        freq.attrs.update(long_name="Frequency",units="Hz")
        trace=file.create_dataset("S21",data=signal);trace.dims[0].attach_scale(point);trace.dims[1].attach_scale(freq)
        trace.attrs.update(long_name="VNA - S21")
        ts=file.create_dataset("timestamp",data=[1.,2.,3.]);ts.dims[0].attach_scale(point)
        file.create_dataset("retained",data=np.array([0,0,1],dtype=np.int8))
    return signal


def single_nc(path):
    signal=np.array([1+2j,3+4j,5+6j,7+8j])
    with h5py.File(path,"w") as file:
        file.attrs.update(schema="labcontrol-nc/1",layout="grid",created="2026-10-22T18:56:00",points=np.array([1]),
                          planned_points=np.array([1]),complete=np.array([1]),project="",user="QEL",tags="單張",labcontrol="{}")
        freq=file.create_dataset("Frequency",data=[4e9,4e9+1e6,4e9+2e6,4e9+3e6]);freq.make_scale("Frequency")
        trace=file.create_dataset("S21",data=signal);trace.dims[0].attach_scale(freq)
        file.create_dataset("timestamp",data=5.)
        file.create_dataset("retained",data=False)
    return signal


def test_point_layout_and_single_trace_convert(tmp_path):
    src=tmp_path/"src";src.mkdir()
    sig=point_nc(src/"async.nc")
    one=single_nc(src/"bg.nc")
    report=convert_batch([src],tmp_path/"out")
    assert report["ok"]==2 and report["errors"]==0, report["files"]
    out={Path(r["output"]).name:Path(r["output"]) for r in report["files"]}
    with h5py.File(out["async.hdf5"],"r") as file:
        np.testing.assert_array_equal(file["Data/Data"][0,1,:],[0.2,0.1,0.3])     # actual setpoints, measurement order
        np.testing.assert_array_equal(file["Traces/VNA - S21"][:,1,:].T,sig.imag)
        assert file["Conversion"].attrs["source_layout"]=="point"
        np.testing.assert_array_equal(file["Conversion/retained"],[0,0,1])
    with h5py.File(out["bg.hdf5"],"r") as file:
        np.testing.assert_array_equal(file["Traces/S21"][:,0,0],one.real)
        assert file["Data/Data"].shape==(1,2,1) and file["Conversion"].attrs["source_layout"]=="single"


def test_non_convertible_files_are_skipped_not_failed(tmp_path):
    src=tmp_path/"src";src.mkdir()
    nc_file(src/"ok.nc")
    with h5py.File(src/"labcontrol_native.hdf5","w") as file:          # Lab Control's own HDF5 next to the .nc
        file.attrs["schema"]="labcontrol/2";file.create_dataset("x",data=[1,2])
    point_nc(src/"twoaxes.nc",two_axes=True)
    report=convert_batch([src],tmp_path/"out")
    by={Path(r["source"]).name:r for r in report["files"]}
    assert by["ok.nc"]["status"]=="ok"
    assert by["labcontrol_native.hdf5"]["status"]=="skipped" and by["labcontrol_native.hdf5"]["reason"]=="not_convertible"
    assert by["twoaxes.nc"]["status"]=="error" and "several swept quantities" in by["twoaxes.nc"]["error"]
    assert report["errors"]==1 and report["skipped"]==1


def test_backups_are_hidden_and_pruned(tmp_path):
    root=tmp_path/"db";root.mkdir()
    initialize_database(root)
    for i in range(8):                                                 # old versions left these beside the DB
        (root/f"Database.hdf5.2026102{i}_000000_000000.bak").write_bytes(b"old")
    for i in range(3):
        merge_database(root,[f"Project/x{i}"])
    assert not list(root.glob("*.bak"))
    kept=sorted((root/converter.BACKUP_DIR).glob("*.bak"))
    assert len(kept)==converter.KEEP_BACKUPS


def test_rebuild_labels_drops_stale_projects(tmp_path):
    src=tmp_path/"src";src.mkdir();nc_file(src/"a.nc")
    root=tmp_path/"out"
    convert_batch([src],root)
    merge_database(root,["Project/mirror","Project/mirror/10/1020"])
    plain=reindex(root)
    with h5py.File(root/"Database.hdf5","r") as file:
        assert "Project/mirror" in file["Labels"].asstr()[()]
    result=reindex(root,rebuild=True)
    with h5py.File(root/"Database.hdf5","r") as file:
        labels=list(file["Labels"].asstr()[()])
    assert "Project/mirror" not in labels and f"Project/10/1020/src" in labels
    assert set(result["removed_labels"])=={"Project/mirror","Project/mirror/10/1020"}


def test_network_lock_unsupported_falls_back(tmp_path, monkeypatch):
    path=tmp_path/"a.h5"
    with h5py.File(path,"w") as file:file.attrs["x"]=1
    real=h5py.File
    def fake(p,mode="r",locking=None,**kw):
        if locking is True:
            raise OSError("Unable to synchronously open file (unable to lock file, errno = 45, error message = 'Operation not supported')")
        return real(p,mode,locking=locking,**kw)
    monkeypatch.setattr(converter.h5py,"File",fake)
    with converter.h5_open(path) as file:
        assert file.attrs["x"]==1
    import safe_io
    assert safe_io.transient(OSError("[WinError 64] The specified network name is no longer available"))
    err=OSError("x");err.winerror=53
    assert safe_io.transient(err)
