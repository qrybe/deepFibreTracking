import torch
import torch.nn as nn
import torch.nn.functional as F
import random
import numpy as np



class ReplayMemory(object):
    """Replay Memory that stores the last size=1,000,000 transitions"""
    def __init__(self, size=1000000, shape=(100,3,3,3), 
                 agent_history_length=1, batch_size=32):
        """
        Args:
            size: Integer, Number of stored transitions
            frame_height: Integer, Height of a frame of an Atari game
            frame_width: Integer, Width of a frame of an Atari game
            agent_history_length: Integer, Number of frames stacked together to create a state
            batch_size: Integer, Number if transitions returned in a minibatch
        """
        self.size = size
        self.agent_history_length = agent_history_length
        self.batch_size = batch_size
        self.count = 0
        self.current = 0
        self.shape = shape
        
        # Pre-allocate memory
        self.actions = np.empty(self.size, dtype=np.int32)
        self.rewards = np.empty(self.size, dtype=np.float32)
        self.states = np.empty((self.size, *self.shape), dtype=np.float32)
        self.new_states = np.empty((self.size, *self.shape), dtype=np.float32)
        self.terminal_flags = np.empty(self.size, dtype=np.bool)

        self._indices = np.empty(self.batch_size, dtype=np.int32)
        
    def add_experience(self, action, state, reward, new_state, terminal):
        """
        Args:
            action: An integer between 0 and env.action_space.n - 1 
                determining the action the agent perfomed
            state: A (100, 3, 3, 3) matrix of interpolated DWI data
            reward: A float determining the reward the agend received for performing an action
            new_state: A (100, 3, 3, 3) matrix of interpolated DWI data
            terminal: A bool stating whether the episode terminated
        """

        self.actions[self.current] = action
        self.states[self.current] = state
        self.rewards[self.current] = reward
        self.new_states[self.current] = new_state
        self.terminal_flags[self.current] = terminal
        self.count = max(self.count, self.current+1)
        self.current = (self.current + 1) % self.size
    
    def get_minibatch(self):
        """
        Returns a minibatch of self.batch_size = 32 transitions
        """
        if self.count < self.batch_size:
            raise ValueError('Not enough memories to get a minibatch')
        
        self._indices = np.random.randint(self.size, size=self.batch_size)    

        return self.states[self._indices], self.actions[self._indices], self.rewards[self._indices], self.new_states[self._indices], self.terminal_flags[self._indices]

 
class DQN(nn.Module):
    """
    Main modell class. First 4 layers are convolutional layers, after that the model is split into the
    advantage and value stream. See the documentation. The convolutional layers are initialized with Kaiming He initialization.
    """
    def __init__(self, n_actions, in_shape, hidden=128):
        """
        Args:
            n_actions: Integer, amount of possible actions of the specific environment
            hidden: Integer, amount of hidden layers (To Do, hidden can change but split_size won't fit anymore)
        """
        super(DQN, self).__init__()
        
        self.n_actions = n_actions
        self.hidden = hidden
        self.in_shape = in_shape

        self.fc_1 = nn.Linear(self.in_shape, self.hidden)
        self.fc_2 = nn.Linear(self.hidden, self.hidden)
        self.fc_3 = nn.Linear(self.hidden, self.hidden)
        self.fc_4 = nn.Linear(self.hidden, self.n_actions)

    def forward(self, x):
        x = x.reshape(x.size(0), -1)
        x = F.relu(self.fc_1(x))
        x = F.relu(self.fc_2(x))
        x = F.relu(self.fc_3(x))
        self.q_values = self.fc_4(x)
        return self.q_values

    def predict_action(self, x):
        q_values = self(x)
        self.best_action = torch.argmax(q_values, 1)
        return self.best_action


class Agent():
    """
    The main agent that is optimized throughout the training process. The class consists of the two models (main and target),
    the optimizer and the memory.
    Args:
        n_actions: Integer, number of possible actions for the environment
        device: PyTorch device to which the models are sent to
        hidden: Integer, amount of hidden neurons in the model
        learning_rate: Float, learning rate for the training process
        gamma: Float, [0:1], discount factor for the loss function
        batch_size: Integer, specify size of each minibatch to process while learning
        agent_history_length: Integer, amount of stacked frames forming one transition
        memory_size: Integer, size of the replay memory
    """
    def __init__(self, n_actions, device, inp_size, hidden=128, learning_rate=0.0000625,
                 gamma=.99, batch_size=32, agent_history_length=4, memory_size=1000000):

        self.n_actions = n_actions
        self.device = device
        self.inp_size = inp_size
        self.hidden = hidden
        self.lr = learning_rate
        self.batch_size = batch_size
        self.agent_history_length= agent_history_length
        self.gamma = gamma
        self.memory_size = memory_size
  
        # Create 2 models
        self.main_dqn = DQN(self.n_actions, np.prod(np.array(self.inp_size)), self.hidden)
        self.target_dqn = DQN(self.n_actions, np.prod(np.array(self.inp_size)), self.hidden)
        # and send them to the device
        self.main_dqn = self.main_dqn.to(self.device)
        self.target_dqn = self.target_dqn.to(self.device)
        
        # Copy weights of the main model to the target model
        self.target_dqn.load_state_dict(self.main_dqn.state_dict())
        # and freeze target model. The model will be updated every now an then (specified in main function) 
        self.target_dqn.eval()

        
        self.replay_memory = ReplayMemory(size=self.memory_size, shape=self.inp_size ,agent_history_length=self.agent_history_length, batch_size=self.batch_size)
        self.optimizer = torch.optim.Adam(self.main_dqn.parameters(), self.lr)

    def optimize(self):
        """
        Optimize the main model.
        Returns:
            Float, the loss between the predicted Q values from the main model and the target Q values from the target model
        """
        # get a minibatch of transitions
        states, actions, rewards, new_states, terminal_flags = self.replay_memory.get_minibatch()

        states = torch.FloatTensor(states).to(self.device)
        next_states = torch.FloatTensor(new_states).to(self.device)
        actions = torch.LongTensor(actions).to(self.device)
        rewards = torch.FloatTensor(rewards).to(self.device)
        terminal_flags = torch.BoolTensor(terminal_flags).to(self.device)

        state_action_values = self.main_dqn(states).gather(1, actions.unsqueeze(-1)).squeeze(-1)

        next_state_actions = self.main_dqn(next_states).max(1)[1]
        next_state_values = self.target_dqn(next_states).gather(1, next_state_actions.unsqueeze(-1)).squeeze(-1)
        next_state_values[terminal_flags] = 0.0

        expected_state_action_values = next_state_values.detach() * self.gamma + rewards

        loss = nn.MSELoss()(state_action_values, expected_state_action_values)
          
        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()
               
        return loss

class Action_Scheduler():
    """Determines an action according to an epsilon greedy strategy with annealing epsilon"""
    def __init__(self, num_actions, model, eps_initial=1, eps_final=0.1, eps_final_step=0.01,
                eps_annealing_steps=1000000, replay_memory_start_size=50000, 
                max_steps=25000000):
        """
        Args:
            num_actions: Integer, number of possible actions
            model: A DQN object
            eps_initial: Float, Exploration probability for the first 
                replay_memory_start_size frames
            eps_final: Float, Exploration probability after 
                replay_memory_start_size + eps_annealing_frames frames
            eps_final_frame: Float, Exploration probability after max_frames frames
            eps_evaluation: Float, Exploration probability during evaluation
            eps_annealing_frames: Int, Number of frames over which the 
                exploration probabilty is annealed from eps_initial to eps_final
            replay_memory_start_size: Integer, Number of frames during 
                which the agent only explores
            max_frames: Integer, Total number of frames shown to the agent
        """
        self.num_actions = num_actions
        self.eps_initial = eps_initial
        self.eps_final = eps_final
        self.eps_final_frame = eps_final_step
        self.eps_annealing_frames = eps_annealing_steps
        self.replay_memory_start_size = replay_memory_start_size
        self.max_frames = max_steps
        self.model = model

        self.eps_current = self.eps_initial
        self.slope = -(self.eps_initial - self.eps_final) / self.eps_annealing_frames
        self.intercept = self.eps_initial - self.slope*self.replay_memory_start_size
        self.slope_2 = - (self.eps_final - self.eps_final_frame) / (self.max_frames - self.eps_annealing_frames - self.replay_memory_start_size)
        self.intercept_2 = self.eps_final_frame - self.slope_2 * self.max_frames

    def get_action(self, frame_number, state, evaluation=False):
        if evaluation:
            self.eps_current = 0.0
        elif frame_number < self.replay_memory_start_size:
            self.eps_current = self.eps_initial
        elif frame_number >= self.replay_memory_start_size and frame_number < self.replay_memory_start_size + self.eps_annealing_frames:
            self.eps_current = self.slope * frame_number + self.intercept
        elif frame_number >= self.replay_memory_start_size + self.eps_annealing_frames:
            self.eps_current = self.slope_2 * frame_number + self.intercept_2

        if np.random.rand(1) < self.eps_current:
            return np.random.randint(0, self.num_actions)
        else:
            return torch.argmax(self.model(state)).item()
