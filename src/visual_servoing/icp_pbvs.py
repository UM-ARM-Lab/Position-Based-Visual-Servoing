import cv2
import numpy as np
import open3d as o3d
from visual_servoing.camera import Camera
import pickle as pkl
import time
import copy
from scipy.spatial.distance import cdist
import tensorflow as tf
from visual_servoing.utils import draw_pose, erase_pos
def SE3(se3):
    # convert TO homogenous TF
    if(se3.shape[0] == 6):
        rot_3x3, _ = cv2.Rodrigues(se3[3:6]) 
        T = np.zeros((4,4)) 
        T[0:3, 0:3] = rot_3x3 
        T[0:3, 3] = se3[0:3] 
        T[3, 3] = 1
        return T
    else:
        rvec, _ = cv2.Rodrigues(se3[0:3, 0:3])
        tvec = se3[0:3, 3].squeeze() 
        return np.hstack((tvec, rvec.squeeze()))


class ICPPBVS:
    # camera: (Instance of a camera following the Camera interface)
    # k_v: (Scaling constant for linear velocity control)
    # k_omega: (Scaling constant for angular velocity control) 
    # start_eef_pose: (Starting pose of end effector link in camera frame (Tcl))
    def __init__(self, camera, k_v, k_omega, model, start_eef_pose, max_joint_velo=0, seg_range=0.1):
        self.seg_range = seg_range
        self.k_v = k_v
        self.k_omega = k_omega
        self.camera = camera
        self.model = o3d.geometry.PointCloud()
        self.model_raw = model
        self.model.points = o3d.utility.Vector3dVector(model)
        self.model.paint_uniform_color([0, 0.651, 0.929])

        self.prev_pose = start_eef_pose
        self.prev_twist = np.zeros(6)
        self.prev_time = time.time()
        self.max_joint_velo = max_joint_velo

        self.pcl = o3d.geometry.PointCloud()
        self.vis = o3d.visualization.Visualizer()
        self.vis.create_window()
        self.vis.add_geometry(self.pcl)
        self.vis.add_geometry(self.model)
        self.model_sdf = pkl.load(open("points_and_sdf.pkl", "rb"))

        self.pose_predict_uids = None

    def draw_registration_result(self):
        #o3d.visualization.draw_geometries([self.pcl, self.model])
        self.vis.update_geometry(self.pcl)
        self.vis.update_geometry(self.model)
        self.vis.poll_events()
        self.vis.update_renderer()

    def get_segmented_pcl(self, pcl_raw, use_prev_twist=False):
        # compute distance between points in new point clouds and points in transformed model
        pcl_raw = np.hstack((pcl_raw.T, np.ones((pcl_raw.shape[1], 1))))
        model_raw = np.hstack((self.model_raw, np.ones((self.model_raw.shape[0], 1))))
        model_tf = model_raw @ self.prev_pose
        keep_list = []
        num_batches = 4
        points_per_batch = pcl_raw.shape[0] // num_batches
        for i in range(num_batches):
            start_idx = int(i * points_per_batch)
            end_idx = int((i+1) * (points_per_batch))
            dist = cdist(pcl_raw[start_idx:end_idx], model_tf, metric='euclidean')
            dist = dist < self.seg_range 
            # aggregate over column dimension if a point was close enough to any other point
            keep_list.append(np.max(dist, axis=1))
        pcl = pcl_raw[:, keep_list]
        return pcl
    
    def round_to_res(self, x, res):
        # helps with stupid numerics issues
        return tf.cast(tf.round(x / res), tf.int64)

    def batch_point_to_idx(self, points, res, origin_point):
        """
    ​
        Args:
            points: [b,3] points in a frame, call it world
            res: [b] meters
            origin_point: [b,3] the position [x,y,z] of the center of the voxel (0,0,0) in the same frame as points
    ​
        Returns:
    ​
        """
        return self.round_to_res((points - origin_point), tf.expand_dims(res, -1))

    def segment(self, pc, sdf, origin_point, res, threshold):
        """
    ​
        Args:
            pc: [n, 3], as set of n (x,y,z) points in the same frame as the voxel grid
            sdf: [h, w, c], signed distance field
            origin_point: [3], the (x,y,z) position of voxel [0,0,0]
            res: scalar, size of one voxel in meters
            threshold: the distance threshold determining what's segmented
    ​
        Returns:
            [m, 3] the segmented points
    ​
        """
        pc = tf.convert_to_tensor(pc, dtype=tf.float32)
        indices = self.batch_point_to_idx(pc, res, origin_point)
        in_bounds = tf.logical_not(tf.logical_or(tf.reduce_any(indices <= 0, -1), tf.reduce_any(indices >= sdf.shape, -1)))
        in_bounds_indices = tf.boolean_mask(indices, in_bounds, axis=0)
        in_bounds_pc = tf.boolean_mask(pc, in_bounds, axis=0)
        distances = tf.gather_nd(sdf, in_bounds_indices)
        close = distances < threshold
        segmented_points = tf.boolean_mask(in_bounds_pc, close, axis=0)
        return segmented_points

    # Will only work in sim
    # get EEF state estimate relative to camera
    def get_eef_state_estimate(self, depth, seg, dt):
        t = time.time()
        # Compute segmented point cloud of eef from depth/seg img
        #print(f'Get pcl {time.time() -t}')
        t = time.time()
        #u, v, depth, ones = self.camera.seg_img((np.arange(16, 30) + 1) << 24, seg, depth)
        #pcl_raw = self.camera.get_pointcloud_seg(depth, u, v, ones)
        pcl_raw = self.camera.get_pointcloud(depth)

        #gripper_pos_tf_est = self.prev_pose @ self.prev_twist
        action = self.prev_twist * dt
        action_tf = SE3(action)
        pose_predict = action_tf @ self.prev_pose
        pose_predict_vis = np.linalg.inv(self.camera.get_view()) @ pose_predict  
        if(self.pose_predict_uids is not None):
            erase_pos(self.pose_predict_uids)
        #draw_pose(pose_predict_vis[0:3, 3], pose_predict_vis[0:3, 0:3], axis_len=0.2, alpha=0.5, mat=True)

        pcl_raw_linkfrm = np.linalg.inv(pose_predict)@np.vstack((pcl_raw,np.ones( (1, pcl_raw.shape[1] ) )))
        pcl_raw_linkfrm = (pcl_raw_linkfrm.T)[:, 0:3]
     
        pcl_seg = self.segment(pcl_raw_linkfrm, self.model_sdf['sdf'], self.model_sdf['origin_point'], self.model_sdf['res'], 0.04)
        pcl_raw = pose_predict@np.hstack((pcl_seg,np.ones( (pcl_seg.shape[0], 1) ))).T
        pcl_raw = pcl_raw[ 0:3, :]

        #t = o3d.geometry.PointCloud()
        #t.points = o3d.utility.Vector3dVector(pcl_raw.T)
        #o3d.visualization.draw_geometries([t])

        #pcl_raw = self.get_segmented_pcl(pcl_raw)

        #print(f'segment pcl {time.time() -t}')
        #t = time.time()

        self.pcl.points = o3d.utility.Vector3dVector(pcl_raw.T)
        self.pcl.paint_uniform_color([1, 0.706, 0])
        #self.pcl.transform(self.prev_pose)
        #self.draw_registration_result()
        #self.pcl.points = o3d.utility.Vector3dVector(pcl_raw.T)
        
        # Run ICP from previous est 
        # we want Tcl, transform of eef link (l) in camera frame, but we do ICP the other way so we estimate Tlc instead
        reg = o3d.pipelines.registration.registration_icp(
            self.pcl, self.model, 0.5, np.linalg.inv(self.prev_pose), o3d.pipelines.registration.TransformationEstimationPointToPoint()
        )
        #print(f'register pcl {time.time() -t}')
        self.pcl.transform(reg.transformation)
        Tcl = np.linalg.inv(reg.transformation)#np.linalg.inv(reg.transformation)
        self.draw_registration_result()
        self.prev_pose = Tcl
        return Tcl
        

    # Executes an iteration of PBVS control and returns a twist command
    # Two: (Pose of target object w.r.t world)
    # Returns the twist command [v, omega] and pose of end effector in world
    def do_pbvs(self, depth, seg, Two, jac, jac_inv, dt=1/240, Tle=np.eye(4), debug=True):
        Tcw = self.camera.get_view()
        Tcl = self.get_eef_state_estimate(depth, seg, dt) 

        Twc = np.linalg.inv(Tcw)
        ctrl = np.zeros(6)
        Twe = Twc @ Tcl @ Tle 

        ctrl = self.get_control(Twe, Two)
        
        # compute the joint velocities using jac_inv and PBVS twist cmd
        q_prime = jac_inv @ ctrl 
        # rescale joint velocities to self.max_joint_velo if the largest exceeds the limit 
        if(np.max(np.abs(q_prime)) > self.max_joint_velo):
            q_prime /= np.max(np.abs(q_prime))
            q_prime *= self.max_joint_velo
        # compute the actual end effector velocity given the limit
        ctrl = jac @ q_prime

        # store results
        self.prev_time = time.time()
        self.prev_twist = ctrl
        return ctrl, Twe
    
    ####################
    # PBVS control law #
    #################### 

    def get_v(self, object_pos, eef_pos):
        return (object_pos - eef_pos) * self.k_v

    def get_omega(self, Rwa, Rwo):
        Rao = np.matmul(Rwa, Rwo.T).T
        Rao_rod, _ = cv2.Rodrigues(Rao)
        return Rao_rod * self.k_omega

    def get_control(self, Twe, Two):
        ctrl = np.zeros(6)
        ctrl[0:3] = self.get_v(Two[0:3, 3], Twe[0:3, 3])
        ctrl[3:6] = np.squeeze(self.get_omega(Twe[0:3, 0:3], Two[0:3, 0:3]))
        return ctrl
