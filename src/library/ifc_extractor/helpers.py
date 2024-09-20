def read_config(config_file: str) -> dict:
    config_dict: dict = {}
    with open(config_file, newline="") as csvfile:
        reader = csv.reader(csvfile, delimiter=";", quotechar="'")

        for row in reader:
            config_dict[row[0]] = row[1]

    return config_dict
