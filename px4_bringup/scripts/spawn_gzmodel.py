#!/usr/bin/env python
import subprocess
import argparse
import utils
import numpy
import os
import rospkg
import rospy
import tf2_ros
import math
import time
import xml.etree.ElementTree as ET


def main():

    # Parse arguments
    parser = argparse.ArgumentParser(description='Spawn robot in Gazebo for SITL')
    parser.add_argument('-model', type=str, default="mbzirc",
                        help='robot model name, must match xacro description folder name')
    parser.add_argument('-id', type=int, default=1,
                        help='robot id, used to compute sim_port')
    parser.add_argument('-x', type=float, default=0.0,
                        help='initial x position')
    parser.add_argument('-y', type=float, default=0.0,
                        help='initial y position')
    parser.add_argument('-z', type=float, default=0.0,
                        help='initial z position')
    parser.add_argument('-Y', type=float, default=0.0,
                        help='initial yaw angle')
    parser.add_argument('-description_package', type=str, default="robots_description",
                        help='robot description package, must follow robots_description file structure')
    parser.add_argument('-material', type=str, default="DarkGrey",
                        help='robot Gazebo/material; \
                        see materials/scripts/gazebo.material (at your gazebo version)')
    parser.add_argument('-backend', type=str, default="mavros",
                        help='backend to use')
    parser.add_argument('-frame_id', type=str, default="map",
                        help='initial position and yaw frame reference; id [map] refers to gazebo origin')
    args, unknown = parser.parse_known_args()
    utils.check_unknown_args(unknown)

    # Get an instance of RosPack with the default search paths
    rospack = rospkg.RosPack()

    # Init ros node
    rospy.init_node('spawn_gzmodel_{}'.format(args.id))

    # Xacro description must be in specified package
    description_dir = rospack.get_path(args.description_package)

    # Create temporary directory for robot sitl stuff
    temp_dir = utils.temp_dir(args.id)
    subprocess.call("mkdir -p " + temp_dir, shell=True)

    # Get udp configuration, depending on id
    udp_config = utils.udp_config(args.id)

    # Check file type (xacro or sdf)
    model_file_type = ""
    for listed_file in sorted(os.listdir(description_dir + "/models/" + args.model)):
        if listed_file == "model.sdf":
            model_file_type = "sdf"
            break
        if listed_file == "model.xacro":
            model_file_type = "xacro"
            break

    if model_file_type == "xacro":
        xacro_description = description_dir + "/models/" + args.model + "/model.xacro"

        # Create urdf from xacro description
        temp_urdf = temp_dir + "/" + args.model + ".urdf"
        xacro_args = "xacro --inorder -o " + temp_urdf + " " + \
        xacro_description + \
        " robot_id:=" + str(args.id) + \
        " enable_mavlink_interface:=true" + \
        " enable_gps_plugin:=true" + \
        " enable_ground_truth:=false" + \
        " enable_logging:=false" + \
        " enable_camera:=false" + \
        " enable_wind:=false" + \
        " mavlink_udp_port:=" + str(udp_config["sim_port"]) + \
        " visual_material:=" + args.material
        xacro_out = open(temp_dir+"/xacro.out", 'w')
        xacro_err = open(temp_dir+"/xacro.err", 'w')
        subprocess.call(xacro_args, shell=True, stdout=xacro_out, stderr=xacro_err)
        xacro_out.close()
        xacro_err.close()

        # Create sdf from urdf
        temp_sdf = temp_dir + "/" + args.model + ".sdf"
        subprocess.call("gz sdf -p " + temp_urdf + " > " + temp_sdf, shell=True)

    elif model_file_type == "sdf":
        model_sdf = description_dir + "/models/" + args.model + "/model.sdf"
        temp_sdf = temp_dir + "/" + args.model + ".sdf"
        subprocess.call("cp " + model_sdf + " " + temp_sdf, shell=True)

        # Change simulation port
        tree = ET.parse(temp_sdf)
        root = tree.getroot()
        model = root.find('model')
        for plugintag in model.findall('plugin'):
            if plugintag.get('name') == 'mavlink_interface':
                porttag = plugintag.find('mavlink_udp_port')
                porttag.text = str(udp_config["sim_port"])

        # Typhoon_h480 patch - TODO use xacro instead
        if args.model == 'typhoon_h480':
            for plugintag in model.findall('plugin'):
                if plugintag.get('name') == 'gimbal_controller':
                    imutag = plugintag.find('gimbal_imu')
                    imutag.text = 'typhoon_h480_' + str(args.id) + '::camera_imu'
            for linktag in model.findall('link'):
                if linktag.get('name') == 'cgo3_camera_link':
                    for sensortag in linktag.findall('sensor'):
                        if sensortag.get('name') == 'camera_imu':
                            sensortag.set('name', 'typhoon_h480_' + str(args.id) + '::camera_imu')

        tree.write(temp_sdf)

    else:
        raise IOError("Couldn't find model.sdf/model.xacro description file")

    # Set gravity=0 for light simulations
    if args.backend == 'light':
        tree = ET.parse(temp_sdf)
        root = tree.getroot()
        model = root.find('model')
        for linktag in model.findall('link'):
            #if linktag.get('name') == 'base_link':
            gravitytag = linktag.find('gravity')
            if gravitytag == None:
                gravitytag = ET.SubElement(linktag,'gravity')
            gravitytag.text = '0'
        tree.write(temp_sdf)

    # Sleep for waiting the world to load
    rospy.wait_for_service('/gazebo/spawn_sdf_model')
    time.sleep(0.4)

    # Minimum z to avoid collision with ground
    z_min = 0.3

    spawn_x = args.x
    spawn_y = args.y
    spawn_z = args.z
    spawn_yaw = args.Y

    if args.frame_id != 'map':
        tf_buffer = tf2_ros.Buffer()
        tf_listener = tf2_ros.TransformListener(tf_buffer)
        try:
            transform_stamped = tf_buffer.lookup_transform('map', args.frame_id, rospy.Time(0), rospy.Duration(10.0))
            if transform_stamped.transform.rotation.x != 0:
                raise ValueError('Only yaw rotations allowed at spawn; rotation.x should be 0, found {}'.format(transform_stamped.transform.rotation.x))
            if transform_stamped.transform.rotation.y != 0:
                raise ValueError('Only yaw rotations allowed at spawn; rotation.y should be 0, found {}'.format(transform_stamped.transform.rotation.y))
            transform_yaw = 2.0 * math.atan2(transform_stamped.transform.rotation.z, transform_stamped.transform.rotation.w)
            new_x = transform_stamped.transform.translation.x + spawn_x * math.cos(transform_yaw) - spawn_y * math.sin(transform_yaw)
            new_y = transform_stamped.transform.translation.y + spawn_x * math.sin(transform_yaw) + spawn_y * math.cos(transform_yaw)
            new_z = transform_stamped.transform.translation.z + spawn_z
            new_yaw = transform_yaw + spawn_yaw
            spawn_x = new_x
            spawn_y = new_y
            spawn_z = new_z
            spawn_yaw = new_yaw
        except (tf2_ros.LookupException, tf2_ros.ConnectivityException, tf2_ros.ExtrapolationException):
            rospy.logerr('Failed to lookup transform from [{}] to [map], ignoring frame_id'.format(args.frame_id))

    # Spawn robot sdf in gazebo
    gzmodel_args = "gz model -f " + temp_sdf + \
    " -m " + args.model + "_" + str(args.id) + \
    " -x " + str(spawn_x) + \
    " -y " + str(spawn_y) + \
    " -z " + str(spawn_z + z_min) + \
    " -Y " + str(spawn_yaw)
    subprocess.call(gzmodel_args, shell=True)

if __name__ == "__main__":
    main()
