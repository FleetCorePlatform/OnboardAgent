#!/usr/bin/env python

# -----------------------------------
# This script can only be executed
#  by the pegasus dev container.
# -----------------------------------

import carb
from isaacsim import SimulationApp

simulation_app = SimulationApp({"headless": False})

import omni.timeline
from omni.isaac.core.world import World
import isaacsim.core.utils.prims as prim_utils

import numpy as np

from pegasus.simulator.params import ROBOTS, SIMULATION_ENVIRONMENTS
from pegasus.simulator.logic.state import State
from pegasus.simulator.logic.graphical_sensors.monocular_camera import MonocularCamera
from pegasus.simulator.logic.backends.ros2_backend import ROS2Backend
from pegasus.simulator.logic.backends.px4_mavlink_backend import (
    PX4MavlinkBackend,
    PX4MavlinkBackendConfig,
)
from pegasus.simulator.logic.vehicles.multirotor import Multirotor, MultirotorConfig
from pegasus.simulator.logic.interface.pegasus_interface import PegasusInterface
from scipy.spatial.transform import Rotation


class PegasusApp:
    def __init__(self):
        self.timeline = omni.timeline.get_timeline_interface()

        self.pg = PegasusInterface()

        self.pg._world = World(**self.pg._world_settings)
        self.world = self.pg.world

        prim_utils.create_prim(
            "/World/Light/DomeLight",
            "DomeLight",
            position=np.array([1.0, 1.0, 1.0]),
            attributes={
                "inputs:intensity": 5e3,
                "inputs:color": (1.0, 1.0, 1.0),
                "inputs:texture:file": "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/4.5/NVIDIA/Assets/Skies/Cloudy/abandoned_parking_4k.hdr",
            },
        )

        self.pg.load_environment(SIMULATION_ENVIRONMENTS["Black Gridroom"])

        config_multirotor0 = MultirotorConfig()
        config_multirotor1 = MultirotorConfig()

        mavlink_config0 = PX4MavlinkBackendConfig(
            {
                "vehicle_id": 0,
                "px4_autolaunch": True,
                "px4_dir": self.pg.px4_path,
                "px4_vehicle_model": self.pg.px4_default_airframe,
            }
        )

        mavlink_config1 = PX4MavlinkBackendConfig(
            {
                "vehicle_id": 1,
                "px4_autolaunch": True,
                "px4_dir": self.pg.px4_path,
                "px4_vehicle_model": self.pg.px4_default_airframe,
            }
        )

        config_multirotor0.backends = [
            PX4MavlinkBackend(mavlink_config0),
            ROS2Backend(
                vehicle_id=0,
                config={
                    "namespace": "drone",
                    "pub_sensors": False,
                    "pub_graphical_sensors": True,
                    "pub_state": True,
                    "sub_control": False,
                },
            ),
        ]

        config_multirotor1.backends = [
            PX4MavlinkBackend(mavlink_config1),
            ROS2Backend(
                vehicle_id=1,
                config={
                    "namespace": "drone",
                    "pub_sensors": False,
                    "pub_graphical_sensors": True,
                    "pub_state": True,
                    "sub_control": False,
                },
            ),
        ]

        config_multirotor0.graphical_sensors = [
            MonocularCamera("camera", config={"update_rate": 60.0})
        ]
        config_multirotor1.graphical_sensors = [
            MonocularCamera("camera", config={"update_rate": 60.0})
        ]

        Multirotor(
            "/World/quadrotor0",
            ROBOTS["Iris"],
            0,
            [0.0, 0.0, 0.07],
            Rotation.from_euler("XYZ", [0.0, 0.0, 0.0], degrees=True).as_quat(),
            config=config_multirotor0,
        )

        Multirotor(
            "/World/quadrotor1",
            ROBOTS["Iris"],
            1,
            [5.0, 0.0, 0.07],
            Rotation.from_euler("XYZ", [0.0, 0.0, 0.0], degrees=True).as_quat(),
            config=config_multirotor1,
        )

        self.world.reset()

        self.stop_sim = False

    def run(self):
        self.timeline.play()

        while simulation_app.is_running() and not self.stop_sim:

            self.world.step(render=True)

        carb.log_warn("PegasusApp Simulation App is closing.")
        self.timeline.stop()
        simulation_app.close()


def main():

    pg_app = PegasusApp()

    pg_app.run()


if __name__ == "__main__":
    main()
