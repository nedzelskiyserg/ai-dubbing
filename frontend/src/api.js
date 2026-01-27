import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5001/api';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const healthCheck = async () => {
  try {
    const response = await api.get('/health');
    return response.data;
  } catch (error) {
    console.error('Health check failed:', error);
    throw error;
  }
};

export const getLogs = async () => {
  try {
    const response = await api.get('/logs');
    return response.data.logs;
  } catch (error) {
    console.error('Failed to get logs:', error);
    throw error;
  }
};

export const clearLogs = async () => {
  try {
    const response = await api.post('/logs/clear');
    return response.data;
  } catch (error) {
    console.error('Failed to clear logs:', error);
    throw error;
  }
};

export const processYouTube = async (url, quality, options) => {
  try {
    const response = await api.post('/process/youtube', {
      url,
      quality,
      ...options,
    });
    return response.data;
  } catch (error) {
    console.error('Failed to process YouTube:', error);
    throw error;
  }
};

export const processFile = async (file, options) => {
  try {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('options', JSON.stringify(options));
    
    const response = await api.post('/process/file', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  } catch (error) {
    console.error('Failed to process file:', error);
    throw error;
  }
};

export const getStatus = async () => {
  try {
    const response = await api.get('/status');
    return response.data;
  } catch (error) {
    console.error('Failed to get status:', error);
    throw error;
  }
};

export const openFolder = async () => {
  try {
    const response = await api.post('/open-folder');
    return response.data;
  } catch (error) {
    console.error('Failed to open folder:', error);
    throw error;
  }
};

export const stopProcessing = async () => {
  try {
    const response = await api.post('/stop');
    return response.data;
  } catch (error) {
    console.error('Failed to stop processing:', error);
    throw error;
  }
};

export default api;
