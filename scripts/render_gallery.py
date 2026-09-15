"""Render the README gallery into docs/images/<example>/{off,on}.png and docs/images/subplots.png.

Needs hardware GL and network access for the PyVista example datasets.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image
import pyvista as pv
from pyvista import examples

import pyvista_render_passes  # noqa: F401  registers plotter.render_passes

OUT = Path(__file__).resolve().parent.parent / 'docs' / 'images'
SIZE = (960, 720)

# The theme's MSAA would give the "off" side anti-aliasing the passes replace.
pv.global_theme.anti_aliasing = None

Setup = Callable[[pv.Plotter], object]


def _look_from(pl: pv.Plotter, direction: tuple[float, float, float], zoom: float) -> None:
    # Fit the scene bounds as seen from a direction, z up.
    center = np.asarray(pl.center)
    pl.camera_position = [tuple(center + np.asarray(direction)), tuple(center), (0, 0, 1)]
    pl.reset_camera()
    pl.camera.zoom(zoom)


def _lidar(pl: pv.Plotter) -> None:
    pl.add_mesh(
        examples.download_lidar(),
        scalars='Elevation',
        style='points',
        point_shape='circle',
        point_size=5,
        show_scalar_bar=False,
    )
    _look_from(pl, (-1, -0.6, 0.3), 2.4)


def _cad_case(pl: pv.Plotter) -> None:
    mesh = examples.download_cad_model_case()
    pl.add_mesh(mesh, smooth_shading=True, split_sharp_edges=True)
    pl.camera.zoom(1.2)


def _notch(pl: pv.Plotter) -> None:
    pl.add_mesh(
        examples.download_notch_stress(),
        scalars='Nodal Stress-normed',
        cmap='turbo',
        show_edges=True,
        edge_color='#202020',
        show_scalar_bar=False,
    )
    _look_from(pl, (0.4, -1, 0.5), 2.8)


def _room(pl: pv.Plotter) -> None:
    room = examples.download_room_surface_mesh()
    pl.add_mesh(
        room, scalars=room.points[:, 1], cmap='viridis', opacity=0.5, show_scalar_bar=False
    )
    pl.camera_position = [(43.6, 49.5, 19.8), (0.0, 2.25, 0.0), (-0.57, 0.7, -0.42)]
    pl.camera.zoom(1.05)


def _st_helens(pl: pv.Plotter) -> None:
    terrain = examples.download_st_helens().warp_by_scalar()
    # 2D actors are placed inside the band that survives the crop in _shot.
    pl.add_mesh(
        terrain,
        cmap='gist_earth',
        scalar_bar_args={
            'title': 'Elevation',
            'fmt': '%.0f',
            'title_font_size': 28,
            'label_font_size': 24,
            'vertical': True,
            'position_x': 0.84,
            'position_y': 0.3,
            'height': 0.4,
        },
    )
    pl.add_text('Mount St Helens', position=(24, 770), font_size=24)
    pl.show_bounds(
        grid='back',
        location='outer',
        font_size=24,
        n_xlabels=3,
        n_ylabels=3,
        n_zlabels=2,
        fmt='%.0f',
    )
    pl.add_axes(viewport=(0, 0.13, 0.2, 0.33))
    _look_from(pl, (-0.7, -1, 0.8), 0.85)


def _on_a_slab(
    pl: pv.Plotter, mesh: pv.PolyData, color: str, view: tuple[float, float, float], zoom: float
) -> None:
    # One scene light casts the shadow; the headlight fills without casting one.
    xs, ys, zs = mesh.bounds_size
    slab = pv.Plane(
        center=(mesh.center[0], mesh.center[1], mesh.bounds.z_min), i_size=xs * 2, j_size=ys * 2
    )
    pl.add_mesh(mesh, color=color, smooth_shading=True, specular=0.3, specular_power=60)
    pl.add_mesh(slab, color='#e0e0e0')
    pl.remove_all_lights()
    pl.add_light(
        pv.Light(
            position=(mesh.center[0] - xs, mesh.center[1] - ys, mesh.bounds.z_max + zs * 2),
            focal_point=mesh.center,
            intensity=0.9,
        )
    )
    pl.add_light(pv.Light(light_type='headlight', intensity=0.3))
    _look_from(pl, view, zoom)


def _bust(pl: pv.Plotter) -> None:
    bust = examples.download_washington_bust().rotate_z(180)
    _on_a_slab(pl, bust, '#e8e2d6', (1, -1.2, 0.55), 1.3)


def _angel(pl: pv.Plotter) -> None:
    _on_a_slab(pl, examples.download_ivan_angel(), '#b9c4cf', (-1, -1.4, 0.35), 1.15)


def _head(pl: pv.Plotter) -> None:
    # The lower part of a CT head under an opaque slice: the slice must occlude the volume.
    head = examples.download_full_head()
    nx, ny, nz = head.dimensions
    lower = head.extract_subset((0, nx - 1, 0, ny - 1, 0, int(0.55 * (nz - 1))))
    pl.add_volume(
        lower,
        cmap='bone',
        clim=(500, 2200),
        opacity=[0, 0, 0.02, 0.05, 0.3, 0.8, 1],
        shade=True,
        show_scalar_bar=False,
    )
    pl.add_mesh(
        lower.slice(normal='z', origin=(*lower.center[:2], lower.bounds.z_max - 0.01)),
        cmap='gray',
        clim=(900, 1500),
        lighting=False,
        show_scalar_bar=False,
    )
    _look_from(pl, (-0.6, -1, 0.4), 1.1)


def _nothing(_pl: pv.Plotter) -> None:
    pass


def _shot(path: Path, scene: Setup, setup: Setup, scale: int = 1) -> None:
    # Rendered square and cropped: VTK's shadow map is wrong on non-square windows.
    # scale > 1 renders smaller and upscales without filtering, so the pixels the
    # pass produced are what the README shows once the browser downsamples.
    width, height = SIZE[0] // scale, SIZE[1] // scale
    pl = pv.Plotter(off_screen=True, window_size=(width, width))
    pl.set_background('white')
    scene(pl)
    setup(pl)
    margin = (width - height) // 2
    image = Image.fromarray(pl.screenshot()[margin : margin + height])
    image.resize(SIZE, Image.Resampling.NEAREST).save(path, optimize=True)
    pl.close()


# example -> (scene, off, on)
EXAMPLES: dict[str, tuple[Setup, Setup, Setup]] = {
    'edl': (_lidar, _nothing, lambda pl: pl.render_passes.enable_edl()),
    'ssao': (_cad_case, _nothing, lambda pl: pl.render_passes.enable_ssao()),
    'shadows': (_angel, _nothing, lambda pl: pl.render_passes.enable_shadows()),
    'ssaa': (_notch, _nothing, lambda pl: pl.render_passes.enable_anti_aliasing()),
    'depth_peeling': (_room, _nothing, lambda pl: pl.render_passes.enable_depth_peeling()),
    'edl_annotation_bypass': (
        _st_helens,
        lambda pl: pl.render_passes.enable_edl().disable_annotation_bypass(),
        lambda pl: pl.render_passes.enable_edl(),
    ),
    'volume_ssaa': (
        _head,
        lambda pl: pl.enable_anti_aliasing('ssaa'),
        lambda pl: pl.render_passes.enable_anti_aliasing(),
    ),
    'photo_real': (_bust, _nothing, lambda pl: pl.render_passes.preset_photo_real()),
}

# Anti-aliasing is judged pixel by pixel, so that pair keeps 1:1 pixels.
PIXEL_SCALE = {'ssaa': 2}


def _subplots(path: Path) -> None:
    # The README's subplot example, verbatim.
    pl = pv.Plotter(off_screen=True, shape=(1, 3), window_size=(1440, 480))
    pl.set_background('white')
    grid = pv.ImageData(dimensions=(5, 5, 5)).explode(0.2)

    pl.subplot(0, 0)
    pl.add_mesh(grid)
    pl.add_text('plain')

    pl.subplot(0, 1)
    pl.add_mesh(grid)
    pl.add_text('EDL')
    pl.render_passes.enable_edl()

    pl.subplot(0, 2)
    pl.add_mesh(grid)
    pl.add_text('SSAO + SSAA')
    pl.render_passes.enable_ssao().enable_anti_aliasing()

    pl.link_views()
    Image.fromarray(pl.screenshot()).save(path, optimize=True)
    pl.close()


def main() -> None:
    for name, (scene, off, on) in EXAMPLES.items():
        folder = OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        scale = PIXEL_SCALE.get(name, 1)
        _shot(folder / 'off.png', scene, off, scale)
        _shot(folder / 'on.png', scene, on, scale)
        print(name)
    _subplots(OUT / 'subplots.png')
    print('subplots')


if __name__ == '__main__':
    main()
