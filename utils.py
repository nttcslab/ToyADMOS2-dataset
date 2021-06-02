import numpy as np
from pathlib import Path
import logging
from itertools import groupby


def get_logger(name, to_file=None, always_renew=False):
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%Y-%m-%d %H:%M', level=logging.INFO)
    logger = logging.getLogger(name)

    if to_file is not None:
        if always_renew and Path(to_file).exists():
            Path(to_file).unlink()
        formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s',
                                      datefmt='%Y-%m-%d %H:%M')
        output_file_handler = logging.FileHandler(to_file)
        output_file_handler.setFormatter(formatter)
        logger.addHandler(output_file_handler)

    return logger


def count_num_of_consecutive_letter(text, target):
    """Count number of `target` letter in text.
    Example:
        >>> count_num_of_consecutive_letter('ToyCar/source_test/abcde_????.wav', '?')
        array([4])
    """
    groups = groupby(text)
    result = [sum(1 for _ in group) for label, group in groups if label == target]
    return np.array(result)


def calc_rms_voladj(wave):
    rms = np.sqrt(np.mean((wave/32767)**2))
    vol_adj = 32767/np.clip(1, 32767, np.abs(wave).max())
    return rms, vol_adj


if __name__ == '__main__':
    logger = get_logger(__name__, to_file='/tmp/test_log.txt')
    logger.info('Info abc')
    logger.debug('Debug abc')
