import hebi
import os
import sys


# ------------------------------------------------------------------------------
# Add the root folder of the repository to the search path for modules
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path = [root_path] + sys.path
# ------------------------------------------------------------------------------


from util.input import listen_for_escape_key, has_esc_been_pressed
from util.math_utils import get_grav_comp_efforts, get_dynamic_comp_efforts, quat2rot
from util.arm import setup_arm_params


enable_logging = True
enable_effort_comp = True

# Mobile Device Setup
phone_family = 'HEBI'
phone_name = 'Mobile IO'

reset_pose_button = 'b1'
quit_demo_button = 'b8'
translation_scale_slider = 'a3'
grip_force_slider = 'a6'

abort_flag = False

while True:
    try
        print('Searching for phone Controller...\n')
        phone_group = lookup.get_group_from_names(phoneFamily, phoneName)        
        print('Phone Found.  Starting up')
        break
    catch
        pause(1.0)
    end

end

# Arm Setup
arm_name = '6-DoF + gripper'
arm_family = 'Arm'
has_gas_spring = False # If you attach a gas spring to the shoulder for extra payload, set this to True.

group, kin, params = setupArm(arm_name, arm_family, has_gas_spring)

ik_seed_pos = params.ik_seed_pos
effort_offset = params.effortOffset
gravity_vec = params.gravity_vec
local_dir = params.local_dir

if params.has_gripper:
    gripper_group = lookup.get_group_from_names(arm_family, 'Spool')
    gripper_group.send_command(params.gripper_gains)
    grip_force_scale = 0.5 * (params.gripper_open_effort - params.gripper_close_effort) 
    grip_force_shift = np.mean([params.gripper_open_effort, params.gripper_close_effort]) 
    gripper_cmd = hebi.GroupCommand(1)

arm_dof_count = kin.dof_count

armDOFs = range(group.size)

# Trajectory
arm_trajGen = HebiTrajectoryGenerator(kin)
arm_trajGen.setMinDuration(1.0) % Min move time for 'small' movements
                             % (default is 1.0)
arm_trajGen.setSpeedFactor(1.0) % Slow down movements to a safer speed.
                             % (default is 1.0)
                             
print('  ')
print('Arm end-effector is now following the mobile device pose.')
print('The control interface has the following commands:')
print('  B1 - Reset/re-align poses.')
print('       This takes the arm to home and aligns with mobile device.')
print('  A3 - Scale down the translation commands to the arm.')
print('       Sliding all the way down means the end-effector only rotates.')
print('  A6 - Control the gripper (if the arm has gripper).')
print('       Sliding down closes the gripper, sliding up opens.')
print('  B8 - Quits the demo.')

cmd = hebi.GroupCommand(group.size)

# Startup
while not abort_flag:
    fbk = group.get_next_feedback()
    fbk_mobile = phone_group.get_next_feedback()

    cmd.position = None
    cmd.velocity = None
    cmd.effort = None

    xyz_scale = [1 1 2].T

    # Start background logging
    if enable_logging:
        group.start_log(os.path.join(local_dir, 'logs'))
        phone_group.start_log(os.path.join(local_dir, 'logs'))

    # Move to current coordinates
    xyz_target_init = [0.5 0.0 0.1].T
    rot_mat_target_init = R_y(pi)

    ik_pos = kin.getIK( 'xyz', xyz_target_init, ...
                               'so3', rot_mat_target_init, ...
                               'initial', ik_seed_pos )

    # Slow trajectories down for the initial move to home position                       
    arm_trajGen.setSpeedFactor(0.5)   
    
    arm_traj = arm_trajGen.newJointMove([fbk.position ik_pos])
    
    # Set trajectories to normal speed for following mobile input
    arm_trajGen.setSpeedFactor(1.0)   
    arm_trajGen.setMinDuration(0.33)  

    t0 = fbk.time
    t = 0

    while t < arm_traj.duration:
        fbk = group.get_next_feedback()
        t = min(fbk.time - t0,arm_traj.getDuration)

        pos, vel, accel = arm_traj.getState(t)
        cmd.position = pos
        cmd.velocity = vel

        if enable_effort_comp:
            # TODO: get_dynamic_comp_efforts
            #dynamics_comp = kin.getDynamicCompEfforts(fbk.position, pos, vel, accel)
            grav_comp = get_grav_comp_efforts(kin, fbk.position, gravity_vec)
            cmd.effort = dynamics_comp + grav_comp + effort_offset

        group.send_command(cmd)

    # Grab initial pose
    fbk_mobile = phone_group.get_next_feedback()
    fbk_mobile = fbk_mobile

    q = [ fbk_mobile.arOrientationW ...
          fbk_mobile.arOrientationX ...
          fbk_mobile.arOrientationY ...
          fbk_mobile.arOrientationZ ]     
    R_init = quat2rot(q)

    xyz_init = [ fbk_mobile.arPositionX
                 fbk_mobile.arPositionY 
                 fbk_mobile.arPositionZ ]

    xyz_phone_new = xyz_init

    end_velocities = np.zeros((1, arm_dof_count))
    end_accels = np.zeros((1, arm_dof_count))

    max_demo_time = inf % sec
    phone_fbk_timer = tic

    time_last = t0
    arm_trajStartTime = t0

    first_run = true

    while not abort_flag:
        fbk = group.get_next_feedback()

        time_now = fbk.time
        dt = time_now - time_last
        time_last = fbk.time

        # Reset the Command Struct
        cmd.effort = None
        cmd.position = None
        cmd.velocity = None

        # Check for restart command
        if fbk_mobile.(resetPoseButton):
            break

        # Check for quit command
        if fbk_mobile.(quitDemoButton):
            abort_flag = true
            break

        if params.has_gripper:
            gripper_cmd.effort = grip_force_scale * fbk_mobile.(grip_force_slider) + grip_force_shift
            gripper_group.send_command(gripper_cmd)

        # Parameter to limit XYZ Translation of the arm if a slider is pulled down.  
        # Pulling all the way down resets translation.
        phone_control_scale = fbk_mobile.(translationScaleSlider)
        if phone_control_scale < 0:
            xyz_init = xyz_phone_new

        # Pose Information for Arm Control
        xyz_phone_new = fbk_mobile.ar_position[0]
                    
        xyz_target = xyz_target_init + phone_control_scale * xyz_scale .* (R_init.T * (xyz_phone_new - xyz_init))

        q = fbk_mobile.ar_orientation[0]
        rot_mat_target = R_init.T * quat2rot(q) * rot_mat_target_init

        # Get state of current trajectory
        if first_run:
            pos = fbk.position_command
            vel = end_velocities
            accel = end_accels
            first_run = false
        else:
            t = time_now - arm_trajStartTime
            pos, vel, accel = arm_traj.get_state(t)
        
        cmd.position = pos
        cmd.velocity = vel

        if enable_effort_comp:
            # TODO: get_dynamic_comp_efforts
            #dynamics_comp = kin.getDynamicCompEfforts(fbk.position, pos, vel, accel)
            grav_comp = get_grav_comp_efforts(kin, fbk.position, gravity_vec)
            cmd.effort = dynamics_comp + grav_comp + effort_offset

        # Force elbow up config
        seed_pos_ik = pos
        seed_pos_ik(3) = abs(seed_pos_ik(3))

        # Find target using inverse kinematics
        ik_pos = kin.getIK( 'xyz', xyz_target, ...
                                   'SO3', rot_mat_target, ...
                                   'initial', seed_pos_ik, ...
                                   'MaxIter', 50 ) 

        # Start new trajectory at the current state        
        phone_hz = 10
        phone_period = 1 / phone_hz
        
        if toc(phone_fbk_timer) > phone_period:
            arm_trajStartTime = time_now
            phone_fbk_timer = tic

            arm_traj = arm_trajGen.newJointMove( [pos ik_pos], ...
                        'Velocities', [vel end_velocities], ...
                        'Accelerations', [accel end_accels])  

        # Send to robot
        group.send_command(cmd)


if enable_logging:
  hebi_log = group.stop_log()

  # Plot tracking / error from the joints in the arm.  Note that there
  # will not by any 'error' in tracking for position and velocity, since
  # this example only commands effort.
  hebi.util.plot_logs(hebi_log, 'position')
  hebi.util.plot_logs(hebi_log, 'velocity')
  hebi.util.plot_logs(hebi_log, 'effort')

  # Plot the end-effectory trajectory and error
  kinematics_analysis(hebilog, kin)

  # Put more plotting code here
