# RTG PLC 与 RCMS 字段对照

基准：本地 RCMS 的 `YsPLCMonitorNewModel.cs`、`ZmqBaseMsg.cs` 与 `doc/plc_protocol.txt`。
对照：用户提供的《plc服务读写通信指令协议说明v2.0.2(1).docx》中的 PLC 参数 content。
本表只比较参数/反馈字段，未对所有写指令兼容性作出承诺。两份文档存在缺引号、重复字段和注释不完整的情况，不能仅凭文件名判断新旧。

## 主要映射和语义差异

| Word 字段 | RCMS 字段 | 含义/类型/处理 | 当前模拟服务 |
|---|---|---|---|
| `big_car_pos` | `bigcar_elec_distance / bigcar_engine_distance` | 大车位置，double，米。场景只有一个连续大车编码器，两侧均来自这个模拟值。 | 已发布 |
| `big_car_vel` | `bigcar1_vel_fb / bigcar2_vel_fb` | 实际大车速度，double，米/秒。RCMS 正值向左，旧字段正值沿场景工程距离增加（向右），需反号。两侧来自同一模拟轴。 | 已发布 |
| `big_car_handle_value` | `bigcar_handle_vel` | 大车手柄速度给定，double，米/秒，RCMS 正值向左。bigcarvel 第一项独立保留，不用实际反馈替代。 | 已发布 |
| `little_car_pos` | `smallcar_pos` | 小车实际位置，double，米。 | 已发布 |
| `little_car_vel` | `smallcar_vel` | 小车实际速度，double，米/秒。 | 已发布 |
| `rope_pos` | `lifting_pos` | 含义转换：Word 为绳长，RCMS 为起升高度。当前模拟绳长=参考高度−起升高度。 | 已发布高度 |
| `rope_vel` | `lifting_vel` | 含义转换：RCMS 为高度变化率，向上为正；Word 绳长模式向下为正，两者符号相反。 | 已发布高度速度 |
| `tool_on_state` | `sprdp_on_state / sprdp_off_state` | 吊具泵开/关反馈，int，互补的虚拟状态。 | 已发布虚拟状态 |
| `big_car_move_left / big_car_move_right` | `bigcar_left_state / bigcar_right_state` | 实际大车左右运动反馈，int，来自实际位置差。 | 已发布 |
| `spreader_up_status` | `lifting_up_state` | 不是严格改名：Word 是手柄起升信号，RCMS 是实际上升反馈。当前 RCMS 字段按实际运动计算。 | 已发布实际运动 |
| `spreader_down_status` | `lifting_down_state` | 不是严格改名：Word 是手柄下降信号，RCMS 是实际下降反馈。 | 已发布实际运动 |
| `little_car_forward_cmd_state / little_car_back_cmd_state` | `smallcar_front_state / smallcar_back_state` | Word 是底层命令反馈，RCMS 是实际运动反馈，不能按收到命令直接置位。 | 已发布实际运动 |
| `big_car_left_forbidden / big_car_right_forbidden` | `not_allow_bigcar_left / not_allow_bigcar_right` | RCMS 禁止方向状态还考虑夹轮器及场景软限位。 | 已发布派生状态 |
| `little_car_forward_limit_state / little_car_back_limit_state` | `smallcar_decelerate_front_state / smallcar_decelerate_back_state` | Word 是方向限速；RCMS 对应减速字段，不能改名为停止限位 limit_smallcar_*。 | 未模拟方向减速 |
| `rope_up_limit_state / rope_down_limit_state` | `lifting_decelerate_state` | Word 为方向限速，RCMS 为起升减速；均区别于 limit_lifting_up/down 停止限位。 | 未模拟减速；另发布软停止限位 |
| `little_car_vel_cb` | `smallcar_vel_fb` | Word 是浮点速度回传；RCMS 模型为 int，PLC 原始缩放未明确，不能直接强转。 | 未映射原始反馈 |
| `rope_vel_cb` | `lifting_vel_fb` | Word 是浮点回传；RCMS 模型为 int，原始缩放和方向需实际 PLC 约定。 | 未映射原始反馈 |
| `little_car_handle_value / spreader_handle_value` | `smallcar_fb / lifting_fb` | RCMS 是 int 原始给定反馈；不能把米/秒浮点给定直接当原始编码。 | 未映射原始反馈 |
| `big_car_move_left_state / big_car_move_right_state` | `无直接一一对应` | Word 是方向禁止/减速编码；RCMS 拆为禁止、速度控制、方向运动等字段。 | 禁止状态已拆分 |
| `big_car_brake_status` | `bigcar_brake1 / bigcar_brake2` | 一个总制动状态与两个独立制动反馈不同，模型未模拟两套制动器。 | 未映射制动传感器 |
| `bypass_status / big_car_by_pass_state` | `bypass_fb / bigcar_bypass_fb` | 旁路反馈，模型未模拟。 | 未映射 |
| `weight / weight1 / weight2 / weight3 / weight4` | `load` | Word 是总重量/四角重量，单位吨；RCMS load 是 int，文档单位待确认，不能直接换名。 | 未模拟称重 |
| `tool_lr_rotate_dir / tool_lr_rotate_angle` | `tool_l_rotate / tool_r_rotate / rotate_pos` | 角度、方向与动作反馈不同；RCMS rotate_pos 单位也待确认。 | 已发布 tool_l_rotate/tool_r_rotate 命令反馈；角度与左右偏移在 digital_twin.spreader_trim，不映射未定义单位的 rotate_pos |
| `front_beam_light_status 等灯光字段` | `fld1_fb / fld2_fb` | Word 按位置区分灯组；RCMS 为投光灯 1/2，没有明确设备映射。 | 未映射灯光反馈 |
| `无` | `ctr_off_state` | 控制断，int，等于 1−ctr_on_state。 | 已补齐 |
| `无严格对应` | `limit_smallcar_front / limit_smallcar_back / limit_lifting_up / limit_lifting_down` | int，场景工程坐标的停止软限位；区别于限速信号。 | 已补齐 |
| `无` | `current_field_num / current_bay_num` | 当前场号/贝号；RCMS 三维大车定位直接依赖这两个字段，连续大车距离不能代替。 | 尚未从场景推导发布 |

起升示例：参考高度 18 米、吊具高度 11 米时，`lifting_pos=11`，原 Word 模式 `rope_pos=7`。RCMS 双侧大车位置是同一模拟编码器的两个输出字段，不代表建模了双侧 GNSS 偏差。

未建模的称重、防撞、GNSS、着箱、独立制动传感器不填造实时值。RCMS 的非 nullable 数值字段在缺失时会默认显示 0，不能把它当成真实测量。

吊具微调还发布 `to_mid_state`（旋转零位反馈，1 表示在零位）。
`digital_twin.spreader_trim` 提供世界角度 yaw_deg、世界米值 shift_m、两轴实际速度、
回零状态和限位；正值为左旋/左移。完整操作与超时说明见 [ZMQ 接口](RTG_PLC_ZMQ.md)。

## 两份协议同名字段

启用本项目 `sim_physics=1` 后，`drop_state` 来自数字场景四脚接触/负载支撑判断，
`lock_state/unlock_state` 来自 PhysX 固定约束状态；关闭时保持原有虚拟锁反馈，
`drop_state` 仍列为不支持。新增详细数据在 `digital_twin.physics`，不会将它们伪装为实体 PLC 传感器。

共 13 个字段。以下按精确字段名比对；“仅一方存在”不表示可直接改名或删除。

| 字段 | RCMS 模型类型 |
|---|---|
| `CRC` | int |
| `ctr_mod_auto` | int |
| `ctr_mod_local` | int |
| `ctr_mod_manual` | int |
| `ctr_mod_remote` | int |
| `ctr_on_state` | int |
| `drop_state` | int |
| `lock_state` | int |
| `plc_exception_status` | int |
| `tool_20f` | int |
| `tool_40f` | int |
| `tool_45f` | int |
| `unlock_state` | int |

## 仅 Word 存在的字段

共 141 个字段。以下按精确字段名比对；“仅一方存在”不表示可直接改名或删除。

| 字段 | RCMS 模型类型 |
|---|---|
| `PLC_Time_Stamp` | 该接收模型无此字段 |
| `PLC_long_time` | 该接收模型无此字段 |
| `beam_latch_status` | 该接收模型无此字段 |
| `beam_stilt_angle` | 该接收模型无此字段 |
| `beam_stilt_speed` | 该接收模型无此字段 |
| `big_camara_control_state` | 该接收模型无此字段 |
| `big_car_brake_status` | 该接收模型无此字段 |
| `big_car_by_pass_state` | 该接收模型无此字段 |
| `big_car_clamp_status` | 该接收模型无此字段 |
| `big_car_handle_value` | 该接收模型无此字段 |
| `big_car_left_forbidden` | 该接收模型无此字段 |
| `big_car_left_limit` | 该接收模型无此字段 |
| `big_car_light_status` | 该接收模型无此字段 |
| `big_car_move_forbidden_state` | 该接收模型无此字段 |
| `big_car_move_left` | 该接收模型无此字段 |
| `big_car_move_left_state` | 该接收模型无此字段 |
| `big_car_move_right` | 该接收模型无此字段 |
| `big_car_move_right_state` | 该接收模型无此字段 |
| `big_car_move_speed_state` | 该接收模型无此字段 |
| `big_car_pos` | 该接收模型无此字段 |
| `big_car_right_forbidden` | 该接收模型无此字段 |
| `big_car_right_limit` | 该接收模型无此字段 |
| `big_car_skid_status` | 该接收模型无此字段 |
| `big_car_vel` | 该接收模型无此字段 |
| `bypass_status` | 该接收模型无此字段 |
| `camera_down_status` | 该接收模型无此字段 |
| `camera_left_status` | 该接收模型无此字段 |
| `camera_right_status` | 该接收模型无此字段 |
| `camera_up_status` | 该接收模型无此字段 |
| `camera_zoomin_status` | 该接收模型无此字段 |
| `camera_zoomout_status` | 该接收模型无此字段 |
| `car_backpos_motion_status` | 该接收模型无此字段 |
| `car_stoppos_status` | 该接收模型无此字段 |
| `conn_beam_light_status` | 该接收模型无此字段 |
| `dive_plat_door_status` | 该接收模型无此字段 |
| `escalator_light_status` | 该接收模型无此字段 |
| `footpath_light_status` | 该接收模型无此字段 |
| `front_beam_light_status` | 该接收模型无此字段 |
| `handle_signals` | 该接收模型无此字段 |
| `handle_switch_camera` | 该接收模型无此字段 |
| `little_car_auto_vel_resp` | 该接收模型无此字段 |
| `little_car_back_cmd_state` | 该接收模型无此字段 |
| `little_car_back_limit_state` | 该接收模型无此字段 |
| `little_car_forward_cmd_state` | 该接收模型无此字段 |
| `little_car_forward_limit_state` | 该接收模型无此字段 |
| `little_car_handle_value` | 该接收模型无此字段 |
| `little_car_pos` | 该接收模型无此字段 |
| `little_car_real_limit_state` | 该接收模型无此字段 |
| `little_car_stop_pos_status` | 该接收模型无此字段 |
| `little_car_vel` | 该接收模型无此字段 |
| `little_car_vel_cb` | 该接收模型无此字段 |
| `mid_back_beam_light_status` | 该接收模型无此字段 |
| `mid_lock_ss_state` | 该接收模型无此字段 |
| `mid_lock_ud_state` | 该接收模型无此字段 |
| `move_to_tool_action_land_pos_state` | 该接收模型无此字段 |
| `move_to_tool_action_sea_pos_state` | 该接收模型无此字段 |
| `move_to_tool_zero_pos_state` | 该接收模型无此字段 |
| `pitch_auto_manual_state` | 该接收模型无此字段 |
| `pitch_buzzer_alarm` | 该接收模型无此字段 |
| `pitch_control_closing_indicator` | 该接收模型无此字段 |
| `pitch_down_indicator` | 该接收模型无此字段 |
| `pitch_emergency_stop_indicator` | 该接收模型无此字段 |
| `pitch_high_low_speed_state` | 该接收模型无此字段 |
| `pitch_hook_indicator` | 该接收模型无此字段 |
| `pitch_hook_lifting_indicator` | 该接收模型无此字段 |
| `pitch_hook_lifting_limit_state` | 该接收模型无此字段 |
| `pitch_hook_limit1_state` | 该接收模型无此字段 |
| `pitch_hook_limit2_state` | 该接收模型无此字段 |
| `pitch_level_indicator` | 该接收模型无此字段 |
| `pitch_little_car_parking_indicator` | 该接收模型无此字段 |
| `pitch_lowering_convex_stop_limit_state` | 该接收模型无此字段 |
| `pitch_lowering_decam_limit_state` | 该接收模型无此字段 |
| `pitch_normal_indicator` | 该接收模型无此字段 |
| `pitch_position_angle` | 该接收模型无此字段 |
| `pitch_rising_convex_stop_limit_state` | 该接收模型无此字段 |
| `pitch_rising_decam_limit_state` | 该接收模型无此字段 |
| `pitch_rising_stop_position_state` | 该接收模型无此字段 |
| `pitch_slack_rope_limit_state` | 该接收模型无此字段 |
| `pitch_sling_lifting_indicator` | 该接收模型无此字段 |
| `pitch_up_indicator` | 该接收模型无此字段 |
| `repairing` | 该接收模型无此字段 |
| `rope_auto_vel_resp` | 该接收模型无此字段 |
| `rope_down_cmd_state` | 该接收模型无此字段 |
| `rope_down_limit_state` | 该接收模型无此字段 |
| `rope_pos` | 该接收模型无此字段 |
| `rope_real_limit_state` | 该接收模型无此字段 |
| `rope_up_cmd_state` | 该接收模型无此字段 |
| `rope_up_limit_state` | 该接收模型无此字段 |
| `rope_vel` | 该接收模型无此字段 |
| `rope_vel_cb` | 该接收模型无此字段 |
| `save_tool_action_land_pos_state` | 该接收模型无此字段 |
| `save_tool_action_pos_control_state` | 该接收模型无此字段 |
| `save_tool_action_sea_pos_state` | 该接收模型无此字段 |
| `save_tool_zero_pos_state` | 该接收模型无此字段 |
| `small_car_light_status` | 该接收模型无此字段 |
| `spreader_down_status` | 该接收模型无此字段 |
| `spreader_handle_value` | 该接收模型无此字段 |
| `spreader_up_status` | 该接收模型无此字段 |
| `test_delay_state` | 该接收模型无此字段 |
| `tool_anti_sway` | 该接收模型无此字段 |
| `tool_anti_twist` | 该接收模型无此字段 |
| `tool_double_state` | 该接收模型无此字段 |
| `tool_driver1_pos` | 该接收模型无此字段 |
| `tool_driver2_pos` | 该接收模型无此字段 |
| `tool_driver3_pos` | 该接收模型无此字段 |
| `tool_fb_tilt_angle` | 该接收模型无此字段 |
| `tool_fb_tilt_dir` | 该接收模型无此字段 |
| `tool_fb_tilt_set_zero_angle` | 该接收模型无此字段 |
| `tool_fb_tilt_zero_angle` | 该接收模型无此字段 |
| `tool_fb_tilt_zero_dir` | 该接收模型无此字段 |
| `tool_fb_tilt_zero_value` | 该接收模型无此字段 |
| `tool_guide_plate_land_side_l_enable` | 该接收模型无此字段 |
| `tool_guide_plate_land_side_l_status` | 该接收模型无此字段 |
| `tool_guide_plate_land_side_r_enable` | 该接收模型无此字段 |
| `tool_guide_plate_land_side_r_status` | 该接收模型无此字段 |
| `tool_guide_plate_sea_side_l_enable` | 该接收模型无此字段 |
| `tool_guide_plate_sea_side_l_status` | 该接收模型无此字段 |
| `tool_guide_plate_sea_side_r_enable` | 该接收模型无此字段 |
| `tool_guide_plate_sea_side_r_status` | 该接收模型无此字段 |
| `tool_lr_rotate_angle` | 该接收模型无此字段 |
| `tool_lr_rotate_dir` | 该接收模型无此字段 |
| `tool_lr_rotate_set_zero_angle` | 该接收模型无此字段 |
| `tool_lr_rotate_zero_angle` | 该接收模型无此字段 |
| `tool_lr_rotate_zero_dir` | 该接收模型无此字段 |
| `tool_lr_rotate_zero_value` | 该接收模型无此字段 |
| `tool_lr_tilt_angle` | 该接收模型无此字段 |
| `tool_lr_tilt_dir` | 该接收模型无此字段 |
| `tool_lr_tilt_set_zero_angle` | 该接收模型无此字段 |
| `tool_lr_tilt_zero_angle` | 该接收模型无此字段 |
| `tool_lr_tilt_zero_dir` | 该接收模型无此字段 |
| `tool_lr_tilt_zero_value` | 该接收模型无此字段 |
| `tool_on_state` | 该接收模型无此字段 |
| `tool_reset_state` | 该接收模型无此字段 |
| `tool_sgl_state` | 该接收模型无此字段 |
| `total_height` | 该接收模型无此字段 |
| `weight` | 该接收模型无此字段 |
| `weight1` | 该接收模型无此字段 |
| `weight2` | 该接收模型无此字段 |
| `weight3` | 该接收模型无此字段 |
| `weight4` | 该接收模型无此字段 |
| `wind` | 该接收模型无此字段 |

## 仅 RCMS 协议存在的字段

共 171 个字段。以下按精确字段名比对；“仅一方存在”不表示可直接改名或删除。

| 字段 | RCMS 模型类型 |
|---|---|
| `anti_bigcar_left_collision` | int |
| `anti_bigcar_right_collision` | int |
| `anti_fipper_press` | int |
| `anti_truck_lifting` | int |
| `auto_correct_st` | int |
| `bay_direction_devation` | double |
| `bigcar1_fb` | double |
| `bigcar1_vel_fb` | double |
| `bigcar2_fb` | double |
| `bigcar2_vel_fb` | double |
| `bigcar_0_dir` | int |
| `bigcar_16_dir` | int |
| `bigcar_45_dir` | int |
| `bigcar_90_dir` | int |
| `bigcar_angle_devation` | double |
| `bigcar_back_collision_state` | int |
| `bigcar_brake1` | int |
| `bigcar_brake2` | int |
| `bigcar_bypass_fb` | int |
| `bigcar_center_x` | double |
| `bigcar_center_y` | double |
| `bigcar_correction_direction` | int |
| `bigcar_correction_num` | int |
| `bigcar_ctrl_cmd` | int |
| `bigcar_ctrl_direction` | int |
| `bigcar_devation` | double |
| `bigcar_diesel_left_collision` | int |
| `bigcar_diesel_right_collision` | int |
| `bigcar_elec_distance` | double |
| `bigcar_elec_to_target_bay` | double |
| `bigcar_electrical_left_collision` | int |
| `bigcar_electrical_right_collision` | int |
| `bigcar_engine_dirver_antenna_left_x` | double |
| `bigcar_engine_dirver_antenna_left_y` | double |
| `bigcar_engine_dirver_antenna_left_z` | double |
| `bigcar_engine_dirver_antenna_right_x` | double |
| `bigcar_engine_dirver_antenna_right_y` | double |
| `bigcar_engine_dirver_antenna_right_z` | double |
| `bigcar_engine_distance` | double |
| `bigcar_engine_to_target_bay` | double |
| `bigcar_forward_reverse_mark` | int |
| `bigcar_front_collision_state` | int |
| `bigcar_handle_vel` | double |
| `bigcar_left_state` | int |
| `bigcar_manual_trans_cmd` | int |
| `bigcar_right_state` | int |
| `bigcar_speed_ctrl` | int |
| `bigcar_stairs_dirver_antenna_left_x` | double |
| `bigcar_stairs_dirver_antenna_left_y` | double |
| `bigcar_stairs_dirver_antenna_left_z` | double |
| `bigcar_stairs_dirver_antenna_right_x` | double |
| `bigcar_stairs_dirver_antenna_right_y` | double |
| `bigcar_stairs_dirver_antenna_right_z` | double |
| `bigcar_zero_state` | int |
| `buzzer_state` | int |
| `bypass_fb` | int |
| `comm_test` | int |
| `correct_ctrl_state_fb` | int |
| `cross_street_devation_mark` | int |
| `cross_street_mark` | int |
| `ctr_off_state` | int |
| `ctrl_effective` | int |
| `current_bay_num` | int |
| `current_field_num` | string |
| `diesel_engine` | int |
| `double_correction_sys_state` | int |
| `drop_l_state` | int |
| `drop_r_state` | int |
| `elec_center_x` | double |
| `elec_center_y` | double |
| `elec_ctrl_speed` | int |
| `elec_left_laster_state` | int |
| `elec_right_laster_state` | int |
| `engine_ctrl_speed` | int |
| `error_reset` | int |
| `estop_state` | int |
| `field_mark` | int |
| `fld1_fb` | int |
| `fld2_fb` | int |
| `gnss1_devation` | double |
| `gnss1_devation_backup` | double |
| `gnss1_diff_time` | int |
| `gnss1_locate_state` | int |
| `gnss1_satellite_num` | int |
| `gnss2_devation` | double |
| `gnss2_devation_backup` | double |
| `gnss2_diff_time` | int |
| `gnss2_locate_state` | int |
| `gnss2_satellite_num` | int |
| `gnss_speed` | double |
| `land_bypass_fb` | int |
| `laster_system_state` | int |
| `lifting_brake1` | int |
| `lifting_brake2` | int |
| `lifting_decelerate_state` | int |
| `lifting_down_state` | int |
| `lifting_fb` | int |
| `lifting_pos` | double |
| `lifting_up_state` | int |
| `lifting_vel` | double |
| `lifting_vel_fb` | int |
| `lifting_zero_state` | int |
| `limit_lifting_down` | int |
| `limit_lifting_up` | int |
| `limit_smallcar_back` | int |
| `limit_smallcar_front` | int |
| `load` | int |
| `load_collision_prevent` | int |
| `need_trans` | int |
| `not_allow_bigcar_left` | int |
| `not_allow_bigcar_right` | int |
| `not_allow_lifting_down` | int |
| `not_allow_lifting_up` | int |
| `not_allow_smallcar_back` | int |
| `not_allow_smallcar_front` | int |
| `overweight_state` | int |
| `pedestrain_detect` | int |
| `rope_loose_state` | int |
| `rope_pull_data` | int |
| `ros_connect` | int |
| `rotate_pos` | 该接收模型无此字段 |
| `row_direction_devation` | double |
| `rplc_recv_fb` | int |
| `rplc_release_fb` | int |
| `rtg_direction` | int |
| `rtg_locate_model` | int |
| `sea_land_lane_sign` | int |
| `security_level` | int |
| `smallcar_back_state` | int |
| `smallcar_brake` | int |
| `smallcar_decelerate_back_state` | int |
| `smallcar_decelerate_front_state` | int |
| `smallcar_decelerate_state` | int |
| `smallcar_fb` | int |
| `smallcar_front_state` | int |
| `smallcar_pos` | double |
| `smallcar_pos_radar` | double |
| `smallcar_vel` | double |
| `smallcar_vel_fb` | int |
| `smallcar_zero_state` | int |
| `sp_int_lock_state` | int |
| `sprdp_off_state` | int |
| `sprdp_on_state` | int |
| `spreader_bypass_fb` | int |
| `spreader_label` | int |
| `spreader_left_move` | int |
| `spreader_posture_abnormal` | int |
| `spreader_right_move` | int |
| `spreader_row` | int |
| `stairs_center_x` | double |
| `stairs_center_y` | double |
| `stairs_left_laster_state` | int |
| `stairs_right_laster_state` | int |
| `steering_pump_off` | int |
| `target_bay_num_fb` | int |
| `target_bay_pos` | int |
| `target_bay_pos_distance` | double |
| `target_field_num_fb` | string |
| `tire_steering_off` | int |
| `tire_steering_on` | int |
| `to_mid_state` | int |
| `tool_20t40` | int |
| `tool_40t20` | int |
| `tool_l_rotate` | int |
| `tool_pos_l_0` | int |
| `tool_pos_l_limit` | int |
| `tool_pos_r_0` | int |
| `tool_pos_r_limit` | int |
| `tool_r_rotate` | int |
| `truck_cabin_monitor` | int |
| `work_cmd_resp_state` | int |

## RCMS 接收模型额外字段（协议参数段未声明）

共 3 个字段。以下按精确字段名比对；“仅一方存在”不表示可直接改名或删除。

| 字段 | RCMS 模型类型 |
|---|---|
| `ctr_mod_auto_string` | string |
| `security_level_string` | string |
| `tool_pos_avg_0` | double |

## 接入位置

- 参数地址：`tcp://127.0.0.1:5516`，SUB 订阅空主题。
- Smart Port 发布端：`omniverse/scripts/rtg_plc_protocol.py` 的 `PlcEngine.snapshot()`。
- RCMS 反序列化入口：`src/RCMS.DataCenter/RtgDataProvider.cs` 的 `SubClientPlc_ReceiveMsg()`。
- RCMS 大车数字显示：`src/RCMS.Modules/Views/YsPlcMonitorView.xaml`，读取 `bigcar_elec_distance`。
- RCMS 三维定位：`ThreeDDisplayView.xaml.cs` 的 `CalculateRtgOffset()`，读取场号和贝号。
