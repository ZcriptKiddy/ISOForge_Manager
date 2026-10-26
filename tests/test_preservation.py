"""Regresiones de metadatos y protección de fuentes, con ISO reales."""
from pathlib import Path
import io
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
import pycdlib
from pycdlib.dates import DirectoryRecordDate
from file_utils import create_source_item
from packing import create_packing_plan
from iso_builder import build_iso_set, BuildCancelled, IsoBuildError
from source_preservation import open_preserving_access, SourceReadPool


def snapshot(path):
    info=path.stat()
    return (info.st_size, info.st_mtime_ns, info.st_atime_ns,
            getattr(info, 'st_birthtime_ns', None), getattr(info, 'st_file_attributes', None))


class PreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.source=self.root/'originales'
        self.source.mkdir()
        self.output=self.root/'salida'
        self.output.mkdir()
        self.file=self.source/'Información.txt'
        self.payload=('Contenido original.\n'*10000).encode('utf-8')
        self.file.write_bytes(self.payload)
        os.utime(self.file, ns=(1450000000123456700,1550000000765432100))
        self.empty=self.source/'vacío.txt'
        self.empty.write_bytes(b'')
        os.utime(self.empty, ns=(1400000000123456700,1500000000765432100))
        self.plan=create_packing_plan([], [create_source_item(self.source,'directory')],10000000)
        self.before={p:snapshot(p) for p in (self.file,self.empty)}

    def tearDown(self):
        self.temp.cleanup()

    def assert_sources_intact(self):
        self.assertEqual(self.before,{p:snapshot(p) for p in self.before})
        with open_preserving_access(self.file) as stream:
            self.assertEqual(stream.read(),self.payload)
        self.assertEqual(self.before,{p:snapshot(p) for p in self.before})

    def test_real_iso_contents_dates_and_blank_labels(self):
        [output]=build_iso_set(self.plan,self.output)
        self.assert_sources_intact()
        iso=pycdlib.PyCdlib(); iso.open(str(output))
        try:
            self.assertEqual(iso.pvd.volume_identifier.rstrip(b' '),b'')
            self.assertEqual(iso.joliet_vd.volume_identifier.decode('utf-16-be').strip(), '')
            for source in (self.file,self.empty):
                rec=iso.get_record(joliet_path='/originales/'+source.name)
                stamp=DirectoryRecordDate(); stamp.new(source.stat().st_mtime_ns//1000000000)
                self.assertEqual(rec.date.record(),stamp.record())
            for rec in iso.list_children(iso_path='/D0000001'):
                if rec.is_file():
                    self.assertIn(rec.date.record(),[iso.get_record(joliet_path='/originales/'+p.name).date.record() for p in (self.file,self.empty)])
            data=io.BytesIO(); iso.get_file_from_iso_fp(data,joliet_path='/originales/Información.txt')
            self.assertEqual(data.getvalue(),self.payload)
        finally: iso.close()

    def test_cancel_preserves_sources_and_removes_temporaries(self):
        cancel=threading.Event()
        original_read=SourceReadPool.read
        def cancel_after_read(pool, reader, size):
            data=original_read(pool, reader, size)
            if data: cancel.set()
            return data
        with patch.object(SourceReadPool, 'read', cancel_after_read):
            with self.assertRaises(BuildCancelled):
                build_iso_set(self.plan,self.output,cancel_event=cancel)
        self.assert_sources_intact()
        self.assertEqual(list(self.output.iterdir()),[])

    def test_read_failure_preserves_sources_and_existing_output(self):
        existing=self.output/'ISO01.iso'; existing.write_bytes(b'anterior')
        original_read=SourceReadPool.read
        reads=[]
        def fail_after_read(pool, reader, size):
            data=original_read(pool, reader, size)
            if data:
                reads.append(1)
                if len(reads)==2: raise OSError('fallo simulado después de leer')
            return data
        with patch.object(SourceReadPool,'read',fail_after_read):
            with self.assertRaises(IsoBuildError): build_iso_set(self.plan,self.output)
        self.assert_sources_intact()
        self.assertEqual(existing.read_bytes(),b'anterior')
        self.assertEqual(list(self.output.iterdir()),[existing])

    def test_refuse_output_inside_source(self):
        with self.assertRaises(IsoBuildError): build_iso_set(self.plan,self.source)
        self.assert_sources_intact()
        self.assertEqual(sorted(p.name for p in self.source.iterdir()), ['Información.txt','vacío.txt'])

    def test_refuse_overwriting_original_iso(self):
        original=self.output/'ISO01.iso'; original.write_bytes(b'original')
        plan=create_packing_plan([], [create_source_item(original,'file')],10000000)
        with self.assertRaises(IsoBuildError): build_iso_set(plan,self.output)
        self.assertEqual(original.read_bytes(),b'original')

if __name__=='__main__': unittest.main()
