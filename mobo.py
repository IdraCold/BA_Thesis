from import_all import *
import socket
import pickle
import pandas as pd
import time
import matplotlib.pyplot as plt
from operator import itemgetter
import requests

HOST = ''
PORT = 50008
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((HOST, PORT))
s.listen(1)
print('Server starts, waiting for connection...')
conn, addr = s.accept()
print('Connected by', addr)
data = conn.recv(1024)

#Methode für den Serverrequest um die generierten Csv Dateien dort direkt abzuspeichern

def send_csv_file_to_server(file_path, server_url):
    with open(file_path, 'r') as file:
        csv_data = file.read()

    payload = {'csv_data': csv_data}
    response = requests.post(server_url, data=payload)

    if response.text == "success":
        print(f"CSV-Datei {file_path} wurde erfolgreich an den Server gesendet.")
    else:
        print(f"Fehler beim Senden der CSV-Datei {file_path} an den Server.")


file_path = "path/to/your/csv_file.csv" # hier müssen wir den path noch anpassen
server_url = "https://bakrauda.de/save_csv.php"  

send_csv_file_to_server(file_path, server_url)

### iter, init_sample, design_parameter_num, objective_num
received = data.decode("utf-8").split('_')
parameter_raw = received[0].split('/')
print('Prameter', parameter_raw)
parameters_strinfo = []
parameters_info = []
for i in range(len(parameter_raw) ):
    parameters_strinfo.append(parameter_raw[i].split(','))
for strlist in parameters_strinfo:
    parameters_info.append(list(map(float, strlist)))

objective_raw = received[1].split('/')
print('Objective', objective_raw)
objectives_strinfo = []
objectives_info = []
for i in range(len(objective_raw)):
    objectives_strinfo.append(objective_raw[i].split(','))
for strlist in objectives_strinfo:
    objectives_info.append(list(map(float, strlist)))

print("Objectives info", len(objectives_info))

problem_dim = 11 #dimension of the parameters x
num_objs = 4 #dimension of the objectives y

device = torch.device("cpu")

# Reference point in objective function space
#ref_point = torch.tensor([-1. for _ in range(num_objs)]).cuda()
ref_point = torch.tensor([-1. for _ in range(num_objs)]).to(device)
#print("Ref_point", ref_point)

# Design parameter bounds
problem_bounds = torch.zeros(2, problem_dim, **tkwargs)
#print("problem_bounds", problem_bounds)


# initialize the problem bounds
# for i in range(4):
#     problem_bounds[0][i] = parameters_info[i][0]
#     problem_bounds[1][i] = parameters_info[i][1]
problem_bounds[1] = 1

# print(problem_bounds)

start_time = time.strftime("%Y-%m-%d-%H-%M", time.localtime())


# Sample objective function
def objective_function(x_tensor):
    x = x_tensor.cpu().numpy()
    print("x", x)
    print("Parameters_info:", parameters_info)
    send_data = "parameters,"
    for i in range(len(x)):
        send_data += str(
            round((x[i]) * (parameters_info[i][1] - parameters_info[i][0]) + parameters_info[i][0], 3)) + ","

    send_data = send_data[:-2]
    print("Send Data: ", send_data )
    conn.sendall(bytes(send_data, 'utf-8'))

    data = conn.recv(1024)
    received_objective = []
    if data:
        received_objective = list(map(float, data.decode("utf-8").split(',')))
        print("data", received_objective)
    if len(data) == 0:
        print("unity end")
    if (len(received_objective) != num_objs):
        print("recevied objective number not consist")

    print("received: ", received_objective)

    def limit_range(f):
        if (f > 1):
            f = 1
        elif (f < -1):
            f = -1
        return f

    print("Received Objective", len(received_objective))



    fs = []
    # Normalization
    for i in range(num_objs):
        f = (received_objective[i] - objectives_info[i][0]) / (objectives_info[i][1] - objectives_info[i][0])
        f = f * 2 - 1
        if (objectives_info[i][2] == 1):
            f *= -1
        f = limit_range(f)
        fs.append(f)

    return torch.tensor(fs, dtype=torch.float64).to(device)
    #return torch.tensor(fs, dtype=torch.float64).cuda()

#das hier heißt dass die Optimierungsfunktion immer random beginnt und deshalb direkt mit der Applikation verbunden sein muss
# n_samples muss 2(d+1) wobei d = num_objs ist sein (https://botorch.org/tutorials/multi_objective_bo)
def generate_initial_data(n_samples=10):
    # generate training data
    train_x = draw_sobol_samples(
        bounds=problem_bounds, n=1, q=n_samples, seed=torch.randint(1000000, (1,)).item()
    ).squeeze(0)
    # train_obj = objective_function(train_x)

    train_obj = []
    for i, x in enumerate(train_x):
        print(f"initial sample: {i + 1}")
        train_obj.append(objective_function(x))

    train_obj_array = np.array([item.cpu().detach().numpy() for item in train_obj], dtype=np.float64)

   # if train_x.shape[1] < train_obj_array.shape[1]:
   #     train_x = torch.cat((train_x, torch.zeros(train_x.shape[0], train_obj_array.shape[1] - train_x.shape[1])), dim=1)
   # elif train_obj_array.shape[1] < train_x.shape[1]:
   #     train_obj_array = torch.cat((train_obj_array, torch.zeros(train_obj_array.shape[0], train_x.shape[1] - train_obj_array.shape[1])), dim=1)
    print("Shape der Arrays: ", train_x.shape, torch.tensor(train_obj_array).to(device).shape)
    #return train_x, torch.tensor([item.cpu().detach().numpy() for item in train_obj], dtype=torch.float64).cuda()
    return train_x, torch.tensor(train_obj_array).to(device)


def initialize_model(train_x, train_obj):
    # define models for objective and constraint
    model = SingleTaskGP(train_x, train_obj, outcome_transform=Standardize(m=train_obj.shape[-1]))
    mll = ExactMarginalLogLikelihood(model.likelihood, model)
    return mll, model


def optimize_qehvi(model, train_obj, sampler):
    """Optimizes the qEHVI acquisition function, and returns a new candidate and observation."""
    # partition non-dominated space into disjoint rectangles
    partitioning = NondominatedPartitioning(ref_point=ref_point, Y=train_obj)
    acq_func = qExpectedHypervolumeImprovement(
        model=model,
        ref_point=ref_point.tolist(),  # use known reference point 
        partitioning=partitioning,
        sampler=sampler,
    )
    # optimize
    candidates, _ = optimize_acqf(
        acq_function=acq_func,
        bounds=problem_bounds,
        q=BATCH_SIZE,
        num_restarts=NUM_RESTARTS,
        raw_samples=RAW_SAMPLES,  # used for intialization heuristic
        options={"batch_limit": 5, "maxiter": 200, "nonnegative": True},
        sequential=True,
    )
    # observe new values 
    new_x = unnormalize(candidates.detach(), bounds=problem_bounds)
    return new_x


def load_data():
    data = pd.read_csv('2021-08-26-10-22_user_observations.csv')
    #y = torch.tensor(np.array([data["Trust"].values, data["Acceptance"].values, data["Usability"].values, data["MentalLoad"].values]).T).cuda()
    #x = torch.tensor(np.array([data["Trajectory"].values, data["TrajectoryAlpha"].values, data["PedestrianIntention"].values]).T).cuda()
    y = torch.tensor(np.array([data["Trust"].values, data["Acceptance"].values, data["Usability"].values,
                               data["MentalLoad"].values]).T).to(device)
    x = torch.tensor(
        np.array(
            [data["Trajectory"].values, data["TrajectoryAlpha"].values, data["TrajectorySize"].values, data["PedestrianIntention"].values, data["PedestrianIntentionSize"].values, data["SemanticSegmentation"].values, data["SemanticSegmentationAlpha"].values, data["CarStatus"].values, data["CoveredArea"].values, data["CoveredAreaAlpha"].values, data["OccludedCars"].value]).T).to(device)
    return x, y


def mobo_execute(seed, iterations, initial_samples):


    torch.manual_seed(seed)

    hv = Hypervolume(ref_point=ref_point)
    # Hypervolumes
    hvs_qehvi = []

    # Initial Samples
    # train_x_qehvi, train_obj_qehvi = load_data()
    train_x_qehvi, train_obj_qehvi = generate_initial_data(n_samples=initial_samples)

    #train_x_qehvi = train_x_qehvi.cpu()
    #train_obj_qehvi = train_obj_qehvi.cpu()

    # Initialize GP models
    mll_qehvi, model_qehvi = initialize_model(train_x_qehvi, train_obj_qehvi)

    # Compute Pareto front and hypervolume
    pareto_mask = is_non_dominated(train_obj_qehvi)
    pareto_y = train_obj_qehvi[pareto_mask]
    volume = hv.compute(pareto_y)
    hvs_qehvi.append(volume)
    save_xy(train_x_qehvi, train_obj_qehvi, hvs_qehvi)

    print("Y:")

    # Go through the iterations
    for iteration in range(1, iterations + 1):
        print("Iteration: " + str(iteration))
        # Fit Models
        fit_gpytorch_model(mll_qehvi)

        # Define qEI acquisition modules using QMC sampler
        qehvi_sampler = SobolQMCNormalSampler(num_samples=MC_SAMPLES)

        # Optimize acquisition functions and get new observations
        new_x_qehvi = optimize_qehvi(model_qehvi, train_obj_qehvi, qehvi_sampler)
        new_obj_qehvi = objective_function(new_x_qehvi[0])

        # Update training points
        train_x_qehvi = torch.cat([train_x_qehvi, new_x_qehvi])
        train_obj_qehvi = torch.cat([train_obj_qehvi, new_obj_qehvi.unsqueeze(0)])

        # Compute hypervolumes
        pareto_mask = is_non_dominated(train_obj_qehvi)
        pareto_y = train_obj_qehvi[pareto_mask]
        volume = hv.compute(pareto_y)
        hvs_qehvi.append(volume)

        save_xy(train_x_qehvi, train_obj_qehvi, hvs_qehvi)
        # print("mask", pareto_mask)
        # print("pareto y", pareto_y)
        # print("volume", volume)

        # print("trianing x", train_x_qehvi)
        # print("trianing obj", train_obj_qehvi)

        mll_qehvi, model_qehvi = initialize_model(train_x_qehvi, train_obj_qehvi)

    return hvs_qehvi, train_x_qehvi, train_obj_qehvi


def save_object(obj, filename):
    with open(filename, 'wb') as output:  # Overwrites any existing file.
        pickle.dump(obj, output, pickle.HIGHEST_PROTOCOL)


def load_object(filename):
    with open(filename, 'rb') as f:
        data = pickle.load(f)
    return data




def save_xy(x_sample, y_sample, hvs_qehvi):
    # Detect pareto front points
    pareto_mask = is_non_dominated(y_sample)
    pareto_obj = y_sample[pareto_mask]

    x_sample = x_sample.cpu().numpy()
    y_sample = y_sample.cpu().numpy()
    pareto_obj = pareto_obj.cpu().numpy()
    pareto_front = x_sample[pareto_mask.cpu()]

    all_record = np.concatenate((y_sample, x_sample), axis=1)

    f_values = y_sample.copy()
    f_values = np.array([list(x) for x in f_values])

    x_all = f_values[:, 0]
    y_all = f_values[:, 1]
    pareto_obj = pareto_obj[pareto_obj[:, 0].argsort()]
    x_pareto = pareto_obj[:, 0]
    y_pareto = pareto_obj[:, 1]

    # Create parallel coordinates plot

    line_index = list(range(len(pareto_front)))
    pareto_design_parameters = np.concatenate((np.array([line_index]).T, pareto_front), axis=1)

    columns_i = ["iter"]
    for i in range(len(pareto_design_parameters[0]) - 1):
        columns_i.append("x" + str(i + 1))

    design_parameters_pd = pd.DataFrame(data=pareto_design_parameters, index=line_index, columns=columns_i)

    plt.rcParams['figure.max_open_warning'] = 50
    plt.figure(figsize=(15, 6))
    # plt.figure()

    plt.subplot(121)
    plt.title('Objective values')
    plt.scatter(x_all, y_all)
    plt.plot(x_pareto, y_pareto, color='r')
    plt.xlabel('Completion Time')
    plt.ylabel('Accuracy')
    plt.xlim(-1, 1)
    plt.ylim(-1, 1)
    # plt.savefig('../Assets/Resources/opt-process-parato-img', dpi=50)

    # plt.clf()

    # plt.figure()

    plt.subplot(122)
    plt.title('Design parameters')
    pd.plotting.parallel_coordinates(design_parameters_pd, "iter")
    # Save plot
    # plt.savefig('../Assets/Resources/opt-process-design-parameter-img', dpi=50)
    plt.savefig('imgs/observations', dpi=50)
    plt.clf()
    # plt.show()
    plt.figure()
    plt.plot(hvs_qehvi)
    plt.title("Pareto Hypervolume Increase", fontsize=24)
    plt.tick_params(axis='x', labelsize=16)
    plt.tick_params(axis='y', labelsize=16)
    # plt.savefig('../Assets/Resources/opt-process-hyper-img', dpi=50)
    plt.savefig('imgs/hypervolume', dpi=50)
    plt.clf()

    # add new column to identify pareto points
    index_arr = []
    for i in pareto_mask:
        temp = ""
        if (i):
            temp = "TRUE"
        else:
            temp = "FALSE"
        index_arr.append(temp)
    all_record = np.concatenate((all_record, np.array([index_arr]).T), axis=1)
    # np.savetxt('../Assets/Resources/VROptimizer.csv', all_record, delimiter=',', fmt="%s")
    header = np.array(['Trust', 'Acceptance', 'Usability', 'MentalLoad', 'Trajectory', 'TrajectoryAlpha', 'TrajectorySize', 'PedestrianIntention ', 'PedestrianIntentionSize ', 'SemanticSegmentation', 'SemanticSegmentationAlpha', 'CarStatus', 'CoveredArea', 'CoveredAreaAlpha', 'OccludedCars', 'IsPareto'])
    all_record = np.append([header], all_record, axis=0)
    np.savetxt('../Assets/Resources/MOBOLed/{}ObservationsPerEvalution.csv'.format(start_time), all_record,
               delimiter=',', fmt="%s")
    # used for unity auto read data
    np.savetxt('../Assets/Resources/UserObservations.csv', all_record, delimiter=',', fmt="%s")
    np.savetxt('../Assets/Resources/MOBOLed/{}HypervolumePerEvalution.csv'.format(start_time), np.array(hvs_qehvi))


hvs_qehvi, train_x_qehvi, train_obj_qehvi = mobo_execute(SEED, N_ITERATIONS, N_INITIAL)
