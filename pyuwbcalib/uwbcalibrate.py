import numpy as np
from scipy import stats

class UwbCalibrate(object):
    """
    # TODO: Update this and subsequent documentation.
    Object to handle calibration for the DECAR/MRASL UWB modules.

    PARAMETERS:
    -----------
    filename_1: str
        Relative address of the file containing the timestamps of the TWR instances initiated
        by the first tag (hereafter referred to as "tag i").
    filename_2: str
        Relative address of the file containing the timestamps of the TWR instances initiated
        by the second tag (hereafter referred to as "tag j").
    tag_ids: list of ints
        List of IDs of the three tags involved in the calibration procedure.
        The order is as follows:
            1) TWR initializer in filename_1 (tag i).
            2) TWR initializer in filename_2 (tag j).
            3) The tag that never initialized a TWR instance (tag k).
    average: bool
        Flag to indicate whether measurements from static intervals should be averaged out.
    static: bool
        Flag to indicate whether the calibration experiment was done with static intervals.
    thresh: float
        Threshold to detect clock wraps and outliers, in nanoseconds.
    """

    _c = 299702547  # speed of light

    def __init__(self, processed_data, average=False, static=True, thresh = 5e7):
        """
        Constructor
        """
        self.average = average
        self.static = static
        self.thresh = thresh

        # Retrieve attributes from processed_data
        self.num_of_formations = processed_data.num_of_formations
        self.tag_ids = processed_data.tag_ids
        self.twr_type = processed_data.twr_type
        self.num_meas = processed_data.num_meas

        self.num_of_tags = processed_data.num_of_formations

        self.r = processed_data.r
        self.phi = processed_data.phi
        self.mean_gt_distance = processed_data.mean_gt_distance
        self.ts_data = processed_data.ts_data
        self.mean_range_meas = processed_data.mean_range_meas

    def _extract_data(self, filename, initiating_idx, target_idx):
        # TODO: This should all be done in PostProcess
        """
        Reads the stored data and stores it in a dictionary for further processing.

        PARAMETERS:
        -----------
        filename: str
            Relative address of the file containing the timestamps of the TWR instances initiated
            by the tag referred to here as the initiating.
        initiating_idx: int
            The index of the initiating tag (initiator) in self.tag_ids.
        target_idx: int
            The index of the target tag in self.tag_ids.

        RETURNS:
        --------
        dict: A dictionary with the following fields
            initiating_id: int
                ID of the initiating tag.
            target_id: int
                ID of the target tag.
            dt: np.array
                Delta time.
            gt: np.array
                Ground truth data.
            Ra1: np.array
                The delta rx2-tx1 in the initiating tag's clock.
            Ra2: np.array
                The delta rx3-rx2 in the initiating tag's clock.
            Db1: np.array
                The delta tx2-rx1 in the target tag's clock.
            Db2: np.array
                The delta tx3-tx2 in the target board's clock.
            D1: np.array
                The value rx1-tx1 in the initiating board's clock.
            D2: np.array
                The value rx2-tx2 in the target board's clock.
        """
        dict = {
            "initiating_id": self.board_ids[initiating_idx],
            "target_id": self.board_ids[target_idx],
        }

        idx_diff = (
            target_idx - initiating_idx
        )  # this is used to determine how many columns to skip
        first_column = 2 + 11 * (idx_diff - 1)
        last_column = first_column + 9
        # Always read the first column to assert that the initiating board id is right
        columns_to_read = np.concatenate(
            (np.array([0]), np.arange(first_column, last_column))
        )

        # Read the file
        my_data = np.genfromtxt(
            filename, delimiter=",", skip_header=1, usecols=tuple(columns_to_read)
        )

        # Ensure right modules are communicating
        assert my_data[0, 0] == dict["initiating_id"]
        assert my_data[0, 1] == dict["target_id"]

        gt = np.array([])
        Ra1 = np.array([])
        Ra2 = np.array([])
        Db1 = np.array([])
        Db2 = np.array([])

        # Average the static intervals if required
        if self.static is True and self.average is True:
            gap_idx = self._find_mocap_gaps(my_data[:, 2])
            gap_idx = (
                [0] + gap_idx + [np.size(my_data[:, 2])]
            )  # pad the indices with the start and the end

            # Loop and average out sections of static formations
            for idx in range(len(gap_idx) - 1):
                idx_beg = gap_idx[idx]
                idx_end = gap_idx[idx + 1]

                # Ground truth
                gt = np.append(gt, np.mean(my_data[idx_beg:idx_end, 3]))

                # Time stamps
                tx1 = my_data[idx_beg:idx_end, 4]
                rx1 = my_data[idx_beg:idx_end, 5]
                tx2 = my_data[idx_beg:idx_end, 6]
                rx2 = my_data[idx_beg:idx_end, 7]
                tx3 = my_data[idx_beg:idx_end, 8]
                rx3 = my_data[idx_beg:idx_end, 9]

                Ra1 = np.append(Ra1, np.mean(rx2 - tx1))
                Ra2 = np.append(Ra2, np.mean(rx3 - rx2))
                Db1 = np.append(Db1, np.mean(tx2 - rx1))
                Db2 = np.append(Db2, np.mean(tx3 - tx2))

        elif (
            self.static is True
        ):  # Otherwise, average out only the ground truth if static
            gt = my_data[:, 3]

            tx1 = my_data[:, 4]
            rx1 = my_data[:, 5]
            tx2 = my_data[:, 6]
            rx2 = my_data[:, 7]
            tx3 = my_data[:, 8]
            rx3 = my_data[:, 9]

            Ra1 = rx2 - tx1
            Ra2 = rx3 - rx2
            Db1 = tx2 - rx1
            Db2 = tx3 - tx2
            D1 = rx1 - tx1
            D2 = rx2 - tx2

        # Calculate delta time
        max_time_ns = (
            2**32 * (1 / 499200000 / 128) * 1e9
        )  # wrap time since timestamps represented as uint32
        dt = tx1[1:] - tx1[:-1]
        dt = np.hstack(([0], dt))
        dt[dt < 0] = dt[dt < 0] + max_time_ns

        # Correct clock wrap affecting measurements
        wrap_Ra1_bool = Ra1 < 0
        wrap_Ra2_bool = Ra2 < 0
        wrap_Db1_bool = Db1 < 0
        wrap_Db2_bool = Db2 < 0

        Ra1[wrap_Ra1_bool] = Ra1[wrap_Ra1_bool] + max_time_ns
        Ra2[wrap_Ra2_bool] = Ra2[wrap_Ra2_bool] + max_time_ns
        Db1[wrap_Db1_bool] = Db1[wrap_Db1_bool] + max_time_ns
        Db2[wrap_Db2_bool] = Db2[wrap_Db2_bool] + max_time_ns

        # -------------------------------------------------
        gap_idx = self._find_mocap_gaps(my_data[:, 2])
        gap_idx = (
            [0] + gap_idx + [np.size(my_data[:, 2])]
        )  # pad the indices with the start and the end
        for idx in range(len(gap_idx) - 1):
            idx_beg = gap_idx[idx]
            idx_end = gap_idx[idx + 1]

            D1_temp = D1[idx_beg:idx_end]

            D1_rounded = D1[idx_beg:idx_end]
            D1_rounded = np.round(D1_rounded / 1e6, 0) * 1e6
            mode_D1 = stats.mode(D1_rounded)
            D1_rounded = D1_rounded - mode_D1.mode
            idx_wrap_D1 = D1_rounded < -1e3
            D1_temp[idx_wrap_D1] = D1_temp[idx_wrap_D1] + max_time_ns
            idx_wrap_D1 = D1_rounded > 1e3
            D1_temp[idx_wrap_D1] = D1_temp[idx_wrap_D1] - max_time_ns

            D1[idx_beg:idx_end] = D1_temp

        gap_idx = self._find_mocap_gaps(my_data[:, 2])
        gap_idx = (
            [0] + gap_idx + [np.size(my_data[:, 2])]
        )  # pad the indices with the start and the end
        for idx in range(len(gap_idx) - 1):
            idx_beg = gap_idx[idx]
            idx_end = gap_idx[idx + 1]

            D2_temp = D2[idx_beg:idx_end]

            D2_rounded = D2[idx_beg:idx_end]
            D2_rounded = np.round(D2_rounded / 1e6, 0) * 1e6
            mode_D2 = stats.mode(D2_rounded)
            D2_rounded = D2_rounded - mode_D2.mode
            idx_wrap_D2 = D2_rounded < -1e3
            D2_temp[idx_wrap_D2] = D2_temp[idx_wrap_D2] + max_time_ns
            idx_wrap_D2 = D2_rounded > 1e3
            D2_temp[idx_wrap_D2] = D2_temp[idx_wrap_D2] - max_time_ns

            D2[idx_beg:idx_end] = D2_temp

        # -------------------------------------------------

        # Remove outliers
        idx_rows = (np.abs(Ra1) > self.thresh).flatten()
        idx_rows = np.logical_or(idx_rows, (np.abs(Ra2) > self.thresh).flatten())
        idx_rows = np.logical_or(idx_rows, (np.abs(Db1) > self.thresh).flatten())
        idx_rows = np.logical_or(idx_rows, (np.abs(Db2) > self.thresh).flatten())
        idx_rows = idx_rows.flatten()

        dt = np.delete(dt, idx_rows, 0)
        gt = np.delete(gt, idx_rows, 0)
        Ra1 = np.delete(Ra1, idx_rows, 0)
        Ra2 = np.delete(Ra2, idx_rows, 0)
        Db1 = np.delete(Db1, idx_rows, 0)
        Db2 = np.delete(Db2, idx_rows, 0)
        D1 = np.delete(D1, idx_rows, 0)
        D2 = np.delete(D2, idx_rows, 0)

        # Record ground truth and recorded time-stamps
        dict["dt"] = dt
        dict["gt"] = gt
        dict["Ra1"] = Ra1
        dict["Ra2"] = Ra2
        dict["Db1"] = Db1
        dict["Db2"] = Db2
        dict["D1"] = D1
        dict["D2"] = D2

        return dict

    def _find_mocap_gaps(self, mocap_ts):
        """
        Finds time gaps in the Mocap data to indicate a change in the static formation.

        PARAMETERS:
        -----------
        mocap_ts: np.array
            The timestamps recorded from the Mocap.

        RETURNS:
        --------
        list of ints: The indices of the measurements corresponding to the beginning of a new formation.
        """
        diff_ts = np.abs(mocap_ts[1:] - mocap_ts[:-1])
        gap = diff_ts > 10e7
        gap_idx = np.argwhere(gap) + 1
        gap_idx = gap_idx.flatten()

        return gap_idx.tolist()

    def _calculate_skew_gain(self, initiating_idx, target_idx):
        """
        Calculates the K parameter given by Ra2/Db2.
        Gain set to 1 if twr_type == 0.

        PARAMETERS:
        -----------
        initiating_idx: int
            The index of the initiating tag (initiator) in self.tag_ids.
        target_idx: int
            The index of the target tag in self.tag_ids.

        RETURNS:
        --------
        np.array: The K values for all the measurements.
        """
        str_temp = (
            str(self.board_ids[initiating_idx]) + "->" + str(self.board_ids[target_idx])
        )
        data = self.data[str_temp]

        Ra2 = data["Ra2"]
        Db2 = data["Db2"]

        if self.twr_type == 0:
            return Ra2 / Ra2
        else:
            return Ra2 / Db2

    def _setup_A_matrix(self, K, initiating_idx, target_idx):
        """
        Calculates the A matrix for the linear least-squares problem.

        PARAMETERS:
        -----------
        K: np.array
            The skew gain K.
        initiating_idx: int
            The index of the initiating tag (initiator) in self.tag_ids.
        target_idx: int
            The index of the target tag in self.tag_ids.

        RETURNS:
        --------
        2D np.array: The A matrix.
        """
        n = len(K)
        A = np.zeros((n, 3))
        A[:, initiating_idx] += 0.5
        A[:, target_idx] = 0.5 * K

        return A

    def _setup_b_vector(self, K, initiating_idx, target_idx):
        """
        Calculates the b vector for the linear least-squares problem.

        PARAMETERS:
        -----------
        K: np.array
            The skew gain K.
        initiating_idx: int
            The index of the initiating tag (initiator) in self.tag_ids.
        target_idx: int
            The index of the target tag in self.tag_ids.

        RETURNS:
        --------
        np.array: The b vector.
        """
        str_temp = (
            str(self.board_ids[initiating_idx]) + "->" + str(self.board_ids[target_idx])
        )
        data = self.data[str_temp]

        gt = data["gt"]
        Ra1 = data["Ra1"]
        Db1 = data["Db1"]

        b = 1 / self._c * gt * 1e9 - 0.5 * (Ra1) + 0.5 * K * (Db1)

        return np.reshape(b, (len(K), 1))

    def _solve_for_antenna_delays(self, A, b):
        """
        Solves the linear least-squares problem.

        PARAMETERS:
        -----------
        A: 2D np.array
            The A matrix.
        b: np.array
            The b vector.

        RETURNS:
        --------
        np.array: The solution to the Ax=b problem.
        """
        return np.linalg.lstsq(A, b)

    def calibrate_antennas(self):
        """
        Calibrate the antenna delays by formulating and solving a linear least-squares problem.

        RETURNS:
        --------
        dict: Dictionary with 3 fields each for tag z \in {i,j,k}
            Module i: (float)
                Antenna delay for tag i
        """
        K1 = self._calculate_skew_gain(0, 1)
        A1 = self._setup_A_matrix(K1, 0, 1)
        b1 = self._setup_b_vector(K1, 0, 1)

        K2 = self._calculate_skew_gain(0, 2)
        A2 = self._setup_A_matrix(K2, 0, 2)
        b2 = self._setup_b_vector(K2, 0, 2)

        K3 = self._calculate_skew_gain(1, 2)
        A3 = self._setup_A_matrix(K3, 1, 2)
        b3 = self._setup_b_vector(K3, 1, 2)

        A = np.vstack((A1, A2, A3))
        b = np.vstack((b1, b2, b3))

        nan_idx = ~np.isnan(b)
        nan_idx = nan_idx.flatten()
        A = A[nan_idx, :]
        b = b[nan_idx]

        x = self._solve_for_antenna_delays(A, b)[0]
        x = x.flatten()

        print(np.linalg.norm(b))
        print(np.linalg.norm(b - A * np.array([x[0], x[1], x[2]])))

        return {
            "Module " + str(self.board_ids[0]): x[0],
            "Module " + str(self.board_ids[1]): x[1],
            "Module " + str(self.board_ids[2]): x[2],
        }

    def correct_antenna_delay(self, id, delay):
        """
        Modifies the data of this object to correct for the antenna delay of a
        specific module.

        PARAMETERS:
        -----------
        id: int
            Module ID whose antenna delay is to be corrected.
        delay: float
            The amount of antenna delay, in nanoseconds.

        TODO: What about D1 and D2? This seems to be a problem.
              We might have to calibrate for TX and RX delays separately
              if we are to proceed with Kalman filtering with this architecture.
        """
        for key in self.data:
            if int(key.partition("-")[0]) == id:
                self.data[key]["Ra1"] = self.data[key]["Ra1"] + delay
            elif int(key.partition(">")[2]) == id:
                self.data[key]["Db1"] = self.data[key]["Db1"] - delay

    def compute_range_meas(self, id1, id2):
        """
        Only supports reverse double-sided TWR.
        TODO: support more TWR types, such as single-sided TWR.
        """
        for key in self.data:
            cond1 = (
                int(key.partition("-")[0]) == id1 and int(key.partition(">")[2]) == id2
            )
            cond2 = (
                int(key.partition("-")[0]) == id2 and int(key.partition(">")[2]) == id1
            )
            if cond1 or cond2:
                temp = self.data[key]
                if self.twr_type == 0:
                    temp = 0.5 * self._c * (temp["Ra1"] - temp["Db1"]) / 1e9
                else:
                    temp = (
                        0.5
                        * self._c
                        * (temp["Ra1"] - (temp["Ra2"] / temp["Db2"]) * temp["Db1"])
                        / 1e9
                    )
                return temp

    def plot_gt_vs_range(self, id, target):
        pass
