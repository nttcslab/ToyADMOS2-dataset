"""ToyADMOS2: Data mixer tool.

This tool mixes ToyADMOS2 data samples according to your recipe, and creates the final dataset for you.

Usage: mix_dataset.py SRC_FOLDER DEST_FOLDER RECIPE_FILE SNR_DB
    SRC_FOLDER   Path to the ToyADMOS2 folder. 
    DEST_FOLDER  Path to a folder you want to create.
    RECIPE_FILE  Recipe file for describing the use of samples.
    SNR_DB       SNR in dB (-6, 6, or any integer), or `clean`.

One more option is written in the recipe file:
    "Settings & Notes" sheet
        "Shuffle Normal" column
            True or False
            If True, all the _source_ normal samples will be shuffled after mixing all samples.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from tqdm import tqdm
import shutil
import fire
import librosa
from librosa.core.audio import __audioread_load
import soundfile as sf
import warnings
warnings.simplefilter('ignore')

from utils import get_logger, count_num_of_consecutive_letter


### OPTION: Sampling rate of the resulting samples. You can also configure this.
FINAL_SR = 16000

# Global variables.
MAX_DATA_REQUEST = 10
MAX_MICS = 8
logger = None


def process_one(src_pair, src_dirs, rms_pair, dest_file, snr_db):
    """Processes a sample; mix recorded audio with noise audio."""

    src_signal, src_noise, _ = src_pair # signal sample file, noise sample file, mic#
    sig_dir, noise_dir = src_dirs
    sig_rms, noise_rms = rms_pair

    # sig, sr_sig = librosa.load(sig_dir/src_signal, sr=None)
    sig, sr_sig = __audioread_load(sig_dir/src_signal, offset=0.0, duration=None, dtype=np.float32)
    sig = librosa.to_mono(sig) # for making sure
    sig = librosa.resample(sig, orig_sr=sr_sig, target_sr=FINAL_SR)

    if snr_db == 'clean':
        # Clean
        logger.info(f'{src_signal}|{sig_rms:.4f} (clean) -> {dest_file.name}')
        mixed = sig
    else:
        # Mix sounds
        logger.info(f'{src_signal}|{sig_rms:.4f} + {src_noise}|{noise_rms:.4f} -> {dest_file.name} snr={snr_db}dB')

        # noi, sr_noi = librosa.load(noise_dir/src_noise, sr=None)
        noi, sr_noi = __audioread_load(noise_dir/src_noise, offset=0.0, duration=None, dtype=np.float32)
        noi = librosa.to_mono(noi) # for making sure
        noi = librosa.resample(noi, orig_sr=sr_noi, target_sr=FINAL_SR)

        k = sig_rms / noise_rms / 10**(snr_db / 20.)
        mixed = (sig * (1. / (1. + k))) + (noi * (k / (1. + k)))

    finalized = (mixed * 32767.0).astype(np.int16)

    sf.write(dest_file, finalized, FINAL_SR, 'PCM_16')


def process_data_requests(src_df, src_root, dest_root, snr_db, df, rows):
    """Processes one request in the recipe."""

    # Make a list of tuple of noise file and its rms.
    rms_car = src_df[src_df.index.str.match('^C.\d+')].rms.mean()
    rms_train = src_df[src_df.index.str.match('^T.\d+')].rms.mean()

    # Process requests one by one
    rows = ['Folder', 'FileID'] + rows
    count = 0
    for src_dir, src_id, ptn, mics, noise, qty in df[rows].values:
        if ptn == '' or qty == '': continue
        mics = eval(mics) # '[1,2,3]' -> list([1,2,3])
        replaceable = ('R' == str(qty)[0]) # replaceable or not
        qty = int(qty[1:]) if 'R' == str(qty)[0] else int(qty)
        noise = int(noise)
        machine = str(src_dir).split('/')[0]

        # base signal RMS amplitude
        rms = rms_car if src_id[0] == 'C' else rms_train

        # Folders
        src_dir = Path(src_root)/src_dir
        dest_dir = (Path(dest_root)/ptn).parent
        noise_dir = Path(src_root)/f'{machine}/env_noise'

        # Find src machine sounds for each mic
        if replaceable:
            logger.info(f'**WARNING** SAMPLE REPLACEABLE {src_id}')
            srcs = {k: [v for v in src_df.index.values if f'{src_id}_mic{k}' in v] for k in mics}
        else:
            srcs = {k: [v for v in src_df.index.values if f'{src_id}_mic{k}' in v and not src_df.loc[v, "used"]] for k in mics}
        srcs = {k: v for k, v in srcs.items() if len(v) > 0}
        sizes = {k: len(v) for k, v in srcs.items()}
        logger.info(f'Processing {rows[2]}/{qty} {src_dir} {src_id} with samples: {sizes} => {dest_dir}')

        # Find noise files for each mic
        noises = {k: [v for v in src_df.index.values if f'{machine}_N{noise}_mic{k}' in v] for k in mics}
        noises = {k: [v[len(machine) + 1:] for v in noises[k]] for k in mics} # fix noise filenames
        nz_sizes = {k: len(v) for k, v in noises.items()}
        logger.info(f' with noise samples: {nz_sizes}')

        # Check the number of available machine samples
        total_available = sum(sizes.values())
        # YOUR OPTION: Uncomment if you just skip requests that cannot be satisfied.
        # if total_available < qty: ############################
        #     logger.warning(f'!!!!!!! SKIPPING total_available < qty ({total_available} < {qty})')
        #     qty = total_available ############################
        assert total_available >= qty, f'Samples not enough for satisfy your request, # of samples {total_available} < {qty} for pattern: {ptn}'

        # Check the number of available noise samples
        min_num_of_noise = min(list(nz_sizes.values()))
        assert min_num_of_noise > 0, f'(For some of mic) No noise available for satisfy your request, please check your request "qx_nz".'

        # Make a sequential list of mp4 files to use files from each mic equally
        selected_pairs = []
        local_count = 0
        while local_count < qty:
            for k in srcs.keys():
                if len(srcs[k]) == 0:
                    logger.debug(f'SHORT OF: {src_id} mic {k}')
                    continue
                sig = srcs[k].pop(0)
                src_df.loc[sig, 'used'] = True
                selected_pairs.append([sig, np.random.choice(noises[k]), k])
                local_count += 1

        # Create a destination folder
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Iterate for the quantity
        file_ptn = Path(ptn).name
        num_num = count_num_of_consecutive_letter(file_ptn, '?').max()
        assert num_num > 0, f'*** Please set ? in your patterns pattern: {ptn}'
        numstr = '?' * num_num
        num_pos = file_ptn.find(numstr)
        num_search_ptn = file_ptn[:num_pos+num_num] + '*.wav'
        for i in range(qty):
            # Find file number in dest_dir
            existings = sorted(dest_dir.glob(num_search_ptn))
            cur_max = 0 if len(existings) == 0 else int(existings[-1].name[num_pos:num_pos+num_num])
            # Process one file
            cur_filename = file_ptn.replace(numstr, f'%.0{num_num}d' % (cur_max + 1))
            process_one(selected_pairs[i], [src_dir, noise_dir], [rms, src_df.loc[f'{machine}_{selected_pairs[i][1]}', 'rms']], dest_dir/cur_filename, snr_db)
            count += 1
    return count


def do_shuffle_normal(dest_folder):
    """Shuffles normal samples."""

    subdirs = [d for d in Path(dest_folder).glob('*') if d.is_dir()]
    for subdir in subdirs:
        tmp_file = subdir/'temp.wav'
        files = sorted(subdir.glob(f'*/section_*_source_*_normal*.wav'))
        print(f'{subdir} for {len(files)} files, temporary file={tmp_file}')
        print([str(f) for f in files[:3]])
        src = files.copy()
        dest = files.copy()
        np.random.shuffle(src)
        for i in range(len(files)):
            a, b = src[i], dest[i]
            if str(a) == str(b):
                print('skip', i)
                continue
            print(i, end=' ')
            #print(src[i], '<->', dest[i])
            shutil.move(a, tmp_file)
            shutil.move(b, a)
            shutil.move(tmp_file, b)
        print()


def process_recipe_file(src_folder, dest_folder, recipe_file, snr_db):
    """Processes a recipe, main program."""

    global logger
    # Load ToyADMOS2/stat.csv
    try:
        src_df = pd.read_csv(src_folder + '/stat.csv').set_index('filename')
        src_df['used'] = False
    except Exception as e:
        print(f'Cannot read {src_folder + "/stat.csv"}.')
        print(e)
        exit(-1)
    # Load recipe file sheets
    try:
        dfs = pd.read_excel(recipe_file, sheet_name=None, engine='openpyxl')
    except Exception as e:
        print(f'Cannot read {recipe_file}.')
        print(e)
        exit(-1)
    # Read settings
    shuffle_normal = False
    try:
        shuffle_normal = dfs['Settings & Notes']['Shuffle Normal'].values[0]
        assert shuffle_normal == True or shuffle_normal == False
    except:
        print('WARNING: Cannot read "Shuffle Normal" column in the sheet "Settings & Notes".')
    print('** Shuffles normal samples at the end of the process **' if shuffle_normal else '(No shuffle normal)')

    Path(dest_folder).mkdir(parents=True, exist_ok=True)
    count = 0
    logger = get_logger('mixer', to_file=Path(dest_folder)/f'log.txt', always_renew=True)
    logger.info(f'From {src_folder} to {dest_folder} according to {recipe_file}, snr={snr_db}dB')
    for target, df in dfs.items():
        if target[0] == '_' or target == 'Settings & Notes':
            print(f'skipping {target}...')
            continue

        logger.info(f'On {target}:')

        df = df[~pd.isna(df['No.'])].fillna('')
        for req in tqdm(range(MAX_DATA_REQUEST)):
            if f'r{req}_pat' not in df.columns:
                continue

            r_pattern = f'r{req}_pat'
            r_mics = f'r{req}_mics'
            r_noise = f'r{req}_nz'
            r_qty = f'r{req}_qty'

            count += process_data_requests(src_df, src_folder, dest_folder, snr_db, df, [r_pattern, r_mics, r_noise, r_qty])
    
    if shuffle_normal:
        logger.info('Shuffle normal samples...')
        do_shuffle_normal(dest_folder)

    logger.info(f'Processed {count} files.')
    return count


# main
fire.Fire(process_recipe_file)
