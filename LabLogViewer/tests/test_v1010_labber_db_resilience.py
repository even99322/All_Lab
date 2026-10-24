"""v1.0.10: Labber database import (merged from the standalone Labber Converter 0.4.2 tests)."""
import pytest as _pytest

from app.core.labber_db import safe_io as _safe_io


@_pytest.fixture(autouse=True)
def fast_stability_window(monkeypatch):
    # Production waits one second. Individual race tests inject a writer at this boundary.
    monkeypatch.setattr(_safe_io, "STABILITY_SECONDS", 0)


import errno
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys

import h5py
import numpy as np
import pytest

from app.core.labber_db import converter, safe_io
from app.core.labber_db.converter import convert_batch, initialize_database, merge_database, DatabaseBusy
from app.core.labber_db.safe_io import RetryPolicy
from tests.test_v1010_labber_db_convert import nc_file


@pytest.mark.parametrize("stage",["source","output","readback"])
def test_local_sandbox_recovers_copy_timeouts_and_bad_readback(tmp_path,monkeypatch,stage):
    source=tmp_path/"a.nc";nc_file(source)
    before=source.read_bytes()
    copy=safe_io.copy_bytes
    attempts=[]
    def unreliable(incoming,outgoing,cancel=None):
        assert not str(incoming).startswith('/Volumes/')
        assert not str(outgoing).startswith('/Volumes/')
        selected=Path(outgoing).name.startswith("source-" if stage=="source" else ".upload-")
        if selected:
            attempts.append(str(outgoing))
            if len(attempts)<3:
                Path(outgoing).write_bytes(b"partial or corrupted transmission")
                if stage!="readback":raise OSError(errno.ETIMEDOUT,"Operation timed out (simulated NAS)")
                return
        copy(incoming,outgoing,cancel)
    monkeypatch.setattr(safe_io,"copy_bytes",unreliable)
    report=convert_batch([source],tmp_path/"out",retry_policy=RetryPolicy(3,0))
    assert report["ok"]==1 and report["errors"]==0 and len(attempts)==3
    output=Path(report["files"][0]["output"])
    assert safe_io.sha256(output)==report["files"][0]["output_sha256"]
    assert len(report["events"])==2
    assert source.read_bytes()==before
    assert not list((tmp_path/"out").rglob(".upload-*"))


def test_exhausted_output_retry_preserves_existing_file_and_cleans_cache(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    output=Path(first["files"][0]["output"]);before=output.read_bytes()
    cache=tmp_path/"local-cache";cache.mkdir()
    monkeypatch.setattr(converter.tempfile,"tempdir",str(cache))
    copy=safe_io.copy_bytes
    calls=[]
    def fail(incoming,outgoing,cancel=None):
        if Path(outgoing).name.startswith(".upload-"):
            calls.append(outgoing)
            Path(outgoing).write_bytes(b"partial")
            raise OSError(errno.ETIMEDOUT,"simulated output timeout")
        copy(incoming,outgoing,cancel)
    monkeypatch.setattr(safe_io,"copy_bytes",fail)
    report=convert_batch([source],tmp_path/"out",on_conflict="replace",retry_policy=RetryPolicy(3,0))
    assert report["errors"]==1 and len(calls)==3
    assert output.read_bytes()==before
    assert not list(cache.iterdir())
    assert not list(output.parent.glob(".*.tmp"))


@pytest.mark.parametrize("active",["flag","changing"])
def test_active_source_is_rejected_before_any_copy(tmp_path,monkeypatch,active):
    source=tmp_path/"a.nc";nc_file(source)
    if active=="flag":
        with h5py.File(source,"r+") as file:file.attrs["is_running"]=True
    else:
        def writer(seconds,cancel=None):
            with h5py.File(source,"r+") as file:file.attrs["new_measurement_point"]=123
        monkeypatch.setattr(safe_io,"pause",writer)
    copies=[]
    monkeypatch.setattr(safe_io,"copy_bytes",lambda *args:copies.append(args))
    report=convert_batch([source],tmp_path/"out")
    assert report["errors"]==1 and report["files"][0]["reason"]=="source_active"
    assert not copies
    assert list((tmp_path/"out").rglob("*.hdf5"))==[tmp_path/"out/Database.hdf5"]


def test_real_hdf5_writer_lock_refuses_copy_in_local_sandbox(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    program="import h5py,sys\nwith h5py.File(sys.argv[1],'r+',locking=True) as f:\n print('ready',flush=True)\n sys.stdin.readline()\n"
    process=subprocess.Popen([sys.executable,"-u","-c",program,str(source)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert process.stdout.readline().strip()=="ready"
        copies=[]
        monkeypatch.setattr(safe_io,"copy_bytes",lambda *args:copies.append(args))
        report=convert_batch([source],tmp_path/"out")
        assert report["errors"]==1 and report["files"][0]["reason"]=="source_active"
        assert not copies
    finally:
        process.communicate("\n",timeout=10)


def test_source_changed_during_local_conversion_never_publishes(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    write=converter.write_nc
    def writer(*args,**kwargs):
        write(*args,**kwargs)
        with h5py.File(source,"r+") as file:file.attrs["measurement_resumed"]=True
    monkeypatch.setattr(converter,"write_nc",writer)
    report=convert_batch([source],tmp_path/"out")
    assert report["errors"]==1 and report["files"][0]["reason"]=="source_active"
    assert not list((tmp_path/"out").glob("2026/**/*.hdf5"))


def test_resume_after_cancellation_verifies_and_skips_completed_file(tmp_path,monkeypatch):
    sources=[tmp_path/"a.nc",tmp_path/"b.nc"]
    for source in sources:nc_file(source)
    calls=[]
    first=convert_batch(sources,tmp_path/"out",cancel=lambda:bool(calls),progress=lambda done,total,name:calls.append(name) if done==1 else None)
    assert first["cancelled"] and first["ok"]==1
    output=Path(first["files"][0]["output"]);before=output.read_bytes()
    write=converter.write_nc
    converted=[]
    def track(file,temp,source,info):
        converted.append(source.name)
        write(file,temp,source,info)
    monkeypatch.setattr(converter,"write_nc",track)
    second=convert_batch(sources,tmp_path/"out",resume=True)
    assert second["ok"]==1 and second["resumed"]==1 and not second["errors"]
    assert converted==["b.nc"] and output.read_bytes()==before
    assert len(list(output.parent.glob("*.hdf5")))==2


@pytest.mark.parametrize("changed",["source","output","settings"])
def test_resume_does_not_trust_changed_content_or_settings(tmp_path,changed):
    source=tmp_path/"a.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    output=Path(first["files"][0]["output"])
    if changed=="source":
        with h5py.File(source,"r+") as file:file["S21"][0,0]=200+300j
    elif changed=="output":output.write_bytes(b"tampered")
    report=convert_batch([source],tmp_path/"out",resume=True,on_conflict="replace",project="New" if changed=="settings" else "")
    assert report["resumed"]==0 and report["ok"]==1
    with h5py.File(output,"r") as file:
        if changed=="source":assert file["Traces/VNA - S21"][0,0,0]==200


def test_resume_recovers_publish_before_progress_commit_and_dead_local_lock(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    save=converter.ProgressLedger.save
    def crash(self,key,record,signature):
        if record["status"]=="ok":raise OSError("simulated interruption after publish")
        save(self,key,record,signature)
    monkeypatch.setattr(converter.ProgressLedger,"save",crash)
    first=convert_batch([source],tmp_path/"out")
    assert first["errors"]==1
    monkeypatch.setattr(converter.ProgressLedger,"save",save)
    (tmp_path/"out/.labber-converter.lock").write_text(json.dumps({"pid":99999999,"host":socket.gethostname(),"token":"dead"}))
    monkeypatch.setattr(converter,"write_nc",lambda *args:pytest.fail("Must recover the prepared publication without converting"))
    second=convert_batch([source],tmp_path/"out",resume=True)
    assert second["resumed"]==1 and not second["errors"]
    assert len(list((tmp_path/"out").glob("2026/**/*.hdf5")))==1


def test_foreign_host_lock_is_never_removed(tmp_path):
    initialize_database(tmp_path)
    lock=tmp_path/".labber-converter.lock"
    content=json.dumps({"pid":99999999,"host":"another-computer","token":"other"})
    lock.write_text(content)
    with pytest.raises(converter.ConversionError):initialize_database(tmp_path)
    assert lock.read_text()==content


def test_database_change_before_commit_reloads_and_preserves_other_labels_and_nodes(tmp_path,monkeypatch):
    initialize_database(tmp_path)
    merge_database(tmp_path,["Project/Original"])
    revision=converter.database_revision
    calls=[]
    def concurrent(path):
        token=revision(path)
        calls.append(token)
        if len(calls)==3:
            with h5py.File(path,"r+") as file:
                labels=list(file["Labels"].asstr()[()])+["Project/Another person"]
                del file["Labels"]
                file.create_dataset("Labels",data=labels,dtype=h5py.string_dtype())
                file.create_group("External node").attrs["keep"]=42
        return token
    monkeypatch.setattr(converter,"database_revision",concurrent)
    merge_database(tmp_path,["Project/Ours"])
    with h5py.File(tmp_path/"Database.hdf5","r") as file:
        assert {"Project/Original","Project/Another person","Project/Ours"}<=set(file["Labels"].asstr()[()])
        assert file["External node"].attrs["keep"]==42


def test_persistent_database_contention_keeps_output_and_resume_syncs_only(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    merge=converter.merge_database
    def busy(root,labels):
        if labels:raise DatabaseBusy("simulated concurrent writes")
        return merge(root,labels)
    monkeypatch.setattr(converter,"merge_database",busy)
    first=convert_batch([source],tmp_path/"out")
    assert first["pending_sync"]==1 and first["errors"]==0
    output=Path(first["files"][0]["output"]);before=output.read_bytes()
    monkeypatch.setattr(converter,"merge_database",merge)
    monkeypatch.setattr(converter,"write_nc",lambda *args:pytest.fail("Only labels should be synchronized"))
    second=convert_batch([source],tmp_path/"out",resume=True)
    assert second["resumed"]==1 and second["pending_sync"]==0
    assert output.read_bytes()==before
    with h5py.File(tmp_path/"out/Database.hdf5","r") as file:
        assert first["files"][0]["labels"][0] in file["Labels"].asstr()[()]


def test_database_contention_is_bounded(tmp_path,monkeypatch):
    initialize_database(tmp_path)
    calls=[]
    def busy(*args):
        calls.append(1)
        raise DatabaseBusy("simulated contention")
    monkeypatch.setattr(converter,"database_revision",busy)
    with pytest.raises(DatabaseBusy):merge_database(tmp_path,["Project/Ours"])
    assert len(calls)==3


def test_resume_refuses_source_that_changes_during_output_verification(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    first=convert_batch([source],tmp_path/"out")
    output=Path(first["files"][0]["output"]);before=output.read_bytes()
    checksum=converter.sha256
    def writer(path,*args):
        value=checksum(path,*args)
        if Path(path)==output:
            with h5py.File(source,"r+") as file:file.attrs["running"]=True
        return value
    monkeypatch.setattr(converter,"sha256",writer)
    report=convert_batch([source],tmp_path/"out",resume=True)
    assert report["errors"]==1 and report["resumed"]==0
    assert report["files"][0]["reason"]=="source_active"
    assert output.read_bytes()==before


def test_permanent_io_error_is_not_retried():
    calls=[]
    def denied():
        calls.append(1)
        raise PermissionError(errno.EACCES,"denied")
    with pytest.raises(OSError):safe_io.retry_io(denied,"test",RetryPolicy(3,0))
    assert len(calls)==1


def test_cancel_during_cache_leaves_no_measurement_output(tmp_path):
    source=tmp_path/"a.nc";nc_file(source)
    stopped=[]
    report=convert_batch([source],tmp_path/"out",cancel=lambda:bool(stopped),
        activity=lambda event:stopped.append(True) if event["stage"]=="caching" else None)
    assert report["cancelled"] and report["ok"]==0
    assert not list((tmp_path/"out").glob("2026/**/*.hdf5"))
    assert not (tmp_path/"out/.labber-converter.lock").exists()


def test_timeout_after_atomic_rename_is_verified_without_duplicate(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    replace=converter.os.replace
    uncertain=[]
    def timeout_after_commit(incoming,outgoing):
        replace(incoming,outgoing)
        if Path(incoming).name.startswith(".upload-") and not uncertain:
            uncertain.append(outgoing)
            raise OSError(errno.ETIMEDOUT,"simulated timeout after successful rename")
    monkeypatch.setattr(converter.os,"replace",timeout_after_commit)
    report=convert_batch([source],tmp_path/"out",retry_policy=RetryPolicy(3,0))
    assert report["ok"]==1 and report["errors"]==0 and len(uncertain)==1
    assert len(list((tmp_path/"out").glob("2026/**/*.hdf5")))==1
    assert len(report["events"])==1


def test_source_close_timeout_retries_before_output_publication(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    checked=converter.checked_hdf5
    calls=[]
    @contextmanager
    def close_timeout(path):
        with checked(path) as file:yield file
        calls.append(path)
        if len(calls)==1:
            raise RuntimeError("Can't decrement id ref count (unable to close file, errno = 60, Operation timed out)")
    monkeypatch.setattr(converter,"checked_hdf5",close_timeout)
    report=convert_batch([source],tmp_path/"out",retry_policy=RetryPolicy(3,0))
    assert report["ok"]==1 and report["errors"]==0
    assert len(report["events"])==1


def test_database_remains_locked_at_end_reports_pending_and_can_resume(tmp_path,monkeypatch):
    source=tmp_path/"a.nc";nc_file(source)
    open_database=converter.open_database
    write=converter.write_nc
    busy=[]
    @contextmanager
    def locked(path,mode="r"):
        if busy:raise DatabaseBusy("simulated database writer lock")
        with open_database(path,mode) as file:yield file
    def begin_other_writer(*args):
        write(*args)
        busy.append(True)
    monkeypatch.setattr(converter,"open_database",locked)
    monkeypatch.setattr(converter,"write_nc",begin_other_writer)
    first=convert_batch([source],tmp_path/"out")
    assert first["pending_sync"]==1 and first["errors"]==0
    assert not first["database_verified"] and first["database_error"]
    assert Path(first["files"][0]["output"]).is_file()
    busy.clear()
    monkeypatch.setattr(converter,"write_nc",lambda *args:pytest.fail("Resume only syncs labels"))
    second=convert_batch([source],tmp_path/"out",resume=True)
    assert second["resumed"]==1 and second["database_verified"]
