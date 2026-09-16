"""Prop-filter channel reservation."""

from __future__ import annotations

import re

import pytest
import pyvista as pv

import pyvista_render_passes as prp
from pyvista_render_passes import passes
from tests.backend import vtkProp

MAX_CHANNEL = prp.pvPropKeyFilterPass.ChannelMaximum


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(passes, '_RESERVED_CHANNELS', {'annotation': prp.CHANNEL_ANNOTATION})


def test_reservation_is_idempotent_per_name():
    overlay = prp.reserve_prop_filter_channel('pkg.overlay')
    assert overlay == prp.reserve_prop_filter_channel('pkg.overlay')
    assert prp.reserve_prop_filter_channel('pkg.labels') not in {overlay, prp.CHANNEL_ANNOTATION}
    assert prp.reserve_prop_filter_channel('annotation') == prp.CHANNEL_ANNOTATION


def test_conflicting_reservations_raise():
    assert prp.reserve_prop_filter_channel('pkg.overlay', channel=5) == 5
    assert prp.reserve_prop_filter_channel('pkg.overlay', channel=5) == 5
    with pytest.raises(ValueError, match=re.escape("already reserved as 'pkg.overlay'")):
        prp.reserve_prop_filter_channel('other.overlay', channel=5)
    with pytest.raises(ValueError, match=re.escape("already reserved as 'annotation'")):
        prp.reserve_prop_filter_channel('other.overlay', channel=prp.CHANNEL_ANNOTATION)
    with pytest.raises(
        ValueError, match=re.escape("'pkg.overlay' already holds prop-filter channel 5")
    ):
        prp.reserve_prop_filter_channel('pkg.overlay', channel=6)
    with pytest.raises(ValueError, match='channel must be in'):
        prp.reserve_prop_filter_channel('pkg.far', channel=MAX_CHANNEL + 1)
    with pytest.raises(ValueError, match='non-empty string'):
        prp.reserve_prop_filter_channel('')


def test_exhaustion_raises_and_never_hands_out_the_annotation_channel():
    handed_out = [prp.reserve_prop_filter_channel(f'pkg.{i}') for i in range(MAX_CHANNEL)]
    assert prp.CHANNEL_ANNOTATION not in handed_out
    assert sorted(handed_out) == list(range(1, MAX_CHANNEL + 1))
    with pytest.raises(RuntimeError, match='prop-filter channels are reserved'):
        prp.reserve_prop_filter_channel('pkg.one_too_many')


def test_tagging_on_an_unreserved_channel_raises():
    prop = pv.Actor()
    with pytest.raises(ValueError, match='Prop-filter channel 7 is not reserved'):
        prp.set_prop_filter_tag(prop, channel=7)
    assert not prp.prop_filter_tag_is_set(prop, channel=7)
    prp.set_prop_filter_tag(prop)  # the annotation channel is pre-reserved
    prp.set_prop_filter_tag(prop, channel=prp.reserve_prop_filter_channel('pkg.seven', channel=7))
    assert prp.has_prop_filter_tag(prop, channel=7)


def test_a_reserved_channel_partitions_independently_of_annotations():
    channel = prp.reserve_prop_filter_channel('pkg.overlay')
    prop = pv.Plotter().add_mesh(pv.Sphere())
    assert isinstance(prop, vtkProp)
    prp.set_prop_filter_tag(prop, channel=channel)
    assert prp.has_prop_filter_tag(prop, channel=channel)
    assert not prp.prop_filter_tag_is_set(prop)
