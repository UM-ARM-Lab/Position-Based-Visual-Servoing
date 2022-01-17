from src.utils import draw_pose, erase_pos
from src.val import *

# Control end effector position with keyboard


KEY_U = 117
KEY_I = 105
KEY_O = 111
KEY_P = 112
KEY_H = 104
KEY_J = 106
KEY_K = 107
KEY_L = 108
KEY_UP = 65297
KEY_RIGHT = 65296
KEY_DOWN = 65298
KEY_LEFT = 65295

val = Val()
p.setRealTimeSimulation(1)

perturb = np.zeros((6)) 
perturb[0:3] = 0.1
target = val.get_eef_pos("left") + perturb

#target_marker = draw_sphere_marker(target[0:3], 0.01, (1.0, 0.0, 0.0, 1.0))
marker = draw_pose(target[0:3], p.getQuaternionFromEuler(target[3:6]))

while(True):
    marker_new = draw_pose(target[0:3], p.getQuaternionFromEuler(target[3:6]))
    erase_pos(marker)
    marker = marker_new

    events = p.getKeyboardEvents()
    print(events)
    if(KEY_LEFT in events):
        target += np.array([0.01, 0.0, 0.0, 0.0, 0.0, 0.0])
    elif(KEY_RIGHT in events):
        target -= np.array([0.01, 0.0, 0.0, 0.0, 0.0, 0.0])

    if(KEY_UP in events):
        target += np.array([0.00, 0.00, 0.01, 0.0, 0.0, 0.0])
    elif(KEY_DOWN in events):
        target -= np.array([0.00, 0.00, 0.01, 0.0, 0.0, 0.0])

    val.psuedoinv_ik("left", target, val.get_eef_pos("left"))

input()