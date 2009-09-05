# Created By: Virgil Dupras
# Created On: 2006/03/03
# $Id$
# Copyright 2009 Hardcoded Software (http://www.hardcoded.net)
# 
# This software is licensed under the "HS" License as described in the "LICENSE" file, 
# which should be included with this package. The terms are also available at 
# http://www.hardcoded.net/licenses/hs_license

import logging


from hsutil import job
from hsutil.misc import dedupe
from hsutil.str import get_file_ext, rem_file_ext

from . import engine
from .ignore import IgnoreList

(SCAN_TYPE_FILENAME,
SCAN_TYPE_FIELDS,
SCAN_TYPE_FIELDS_NO_ORDER,
SCAN_TYPE_TAG,
SCAN_TYPE_CONTENT,
SCAN_TYPE_CONTENT_AUDIO) = range(6)

SCANNABLE_TAGS = ['track', 'artist', 'album', 'title', 'genre', 'year']

class Scanner(object):
    def __init__(self):
        self.ignore_list = IgnoreList()
        self.discarded_file_count = 0
    
    @staticmethod
    def _filter_matches_by_content(matches, partial, j):
        matched_files = dedupe([m.first for m in matches] + [m.second for m in matches])
        md5attrname = 'md5partial' if partial else 'md5'
        md5 = lambda f: getattr(f, md5attrname)
        for matched_file in j.iter_with_progress(matched_files, 'Analyzed %d/%d matching files'):
            md5(matched_file)
        j.set_progress(100, 'Removing false matches')
        return [m for m in matches if md5(m.first) == md5(m.second)]
    
    def _getmatches(self, files, j):
        j = j.start_subjob(2)
        mf = engine.MatchFactory()
        if self.scan_type != SCAN_TYPE_CONTENT:
            mf.match_similar_words = self.match_similar_words
            mf.weight_words = self.word_weighting
            mf.min_match_percentage = self.min_match_percentage
        if self.scan_type == SCAN_TYPE_FIELDS_NO_ORDER:
            self.scan_type = SCAN_TYPE_FIELDS
            mf.no_field_order = True
        func = {
            SCAN_TYPE_FILENAME: lambda f: engine.getwords(rem_file_ext(f.name)),
            SCAN_TYPE_FIELDS: lambda f: engine.getfields(rem_file_ext(f.name)),
            SCAN_TYPE_TAG: lambda f: [engine.getwords(unicode(getattr(f, attrname))) for attrname in SCANNABLE_TAGS if attrname in self.scanned_tags],
            SCAN_TYPE_CONTENT: lambda f: [str(f.size)],
            SCAN_TYPE_CONTENT_AUDIO: lambda f: [str(f.audiosize)]
        }[self.scan_type]
        for f in j.iter_with_progress(files, 'Read metadata of %d/%d files'):
            if self.size_threshold:
                f.size # pre-read, makes a smoother progress if read here (especially for bundles)
            f.words = func(f)
        if self.size_threshold:
            files = [f for f in files if f.size >= self.size_threshold]
        return mf.getmatches(files, j)
    
    @staticmethod
    def _key_func(dupe):
        return (not dupe.is_ref, -dupe.size)
    
    @staticmethod
    def _tie_breaker(ref, dupe):
        refname = rem_file_ext(ref.name).lower()
        dupename = rem_file_ext(dupe.name).lower()
        if 'copy' in refname and 'copy' not in dupename:
            return True
        if refname.startswith(dupename) and (refname[len(dupename):].strip().isdigit()):
            return True
        return len(dupe.path) > len(ref.path)
    
    def GetDupeGroups(self, files, j=job.nulljob):
        j = j.start_subjob([8, 2])
        for f in [f for f in files if not hasattr(f, 'is_ref')]:
            f.is_ref = False
        logging.info('Getting matches')
        if self.match_factory is None:
            matches = self._getmatches(files, j)
        else:
            matches = self.match_factory.getmatches(files, j)
        logging.info('Found %d matches' % len(matches))
        if not self.mix_file_kind:
            j.set_progress(100, 'Removing false matches')
            matches = [m for m in matches if get_file_ext(m.first.name) == get_file_ext(m.second.name)]
        if self.ignore_list:
            j = j.start_subjob(2)
            iter_matches = j.iter_with_progress(matches, 'Processed %d/%d matches against the ignore list')
            matches = [m for m in iter_matches 
                if not self.ignore_list.AreIgnored(unicode(m.first.path), unicode(m.second.path))]
        if self.scan_type in (SCAN_TYPE_CONTENT, SCAN_TYPE_CONTENT_AUDIO):
            j = j.start_subjob(3 if self.scan_type == SCAN_TYPE_CONTENT else 2)
            matches = self._filter_matches_by_content(matches, partial=True, j=j)
            if self.scan_type == SCAN_TYPE_CONTENT:
                matches = self._filter_matches_by_content(matches, partial=False, j=j)
            # We compared md5. No words were involved.
            for m in matches:
                m.first.words = m.second.words = ['--']
        logging.info('Grouping matches')
        groups = engine.get_groups(matches, j)
        groups = [g for g in groups if any(not f.is_ref for f in g)]
        logging.info('Created %d groups' % len(groups))
        j.set_progress(100, 'Doing group prioritization')
        for g in groups:
            g.prioritize(self._key_func, self._tie_breaker)
        matched_files = dedupe([m.first for m in matches] + [m.second for m in matches])
        self.discarded_file_count = len(matched_files) - sum(len(g) for g in groups)
        return groups
    
    match_factory        = None
    match_similar_words  = False
    min_match_percentage = 80
    mix_file_kind        = True
    scan_type            = SCAN_TYPE_FILENAME
    scanned_tags         = set(['artist', 'title'])
    size_threshold       = 0
    word_weighting       = False

class ScannerME(Scanner): # Scanner for Music Edition
    @staticmethod
    def _key_func(dupe):
        return (not dupe.is_ref, -dupe.bitrate, -dupe.size)
    
